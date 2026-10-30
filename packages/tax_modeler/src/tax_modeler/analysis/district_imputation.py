"""
District-Level SOI Data Imputation

This module implements nearest-neighbor imputation for districts without direct SOI data,
using demographic and economic similarity metrics to find comparable districts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.metrics.pairwise import euclidean_distances
from sklearn.preprocessing import StandardScaler

logger = logging.getLogger(__name__)

@dataclass
class DistrictMetadata:
    """Container for district metadata and features."""
    district_id: str
    has_soi_data: bool
    features: dict[str, float]  # Normalized feature values
    raw_data: dict[str, float]  # Original feature values
    
    @property
    def feature_vector(self) -> np.ndarray:
        """Return feature vector for similarity calculation."""
        return np.array(list(self.features.values()))

class DistrictNearestNeighborImputer:
    """Implements nearest-neighbor imputation for district-level SOI data."""
    
    def __init__(self, min_similarity: float = 0.7, n_neighbors: int = 3):
        """
        Initialize the imputer.
        
        Args:
            min_similarity: Minimum similarity score (0-1) to consider a neighbor
            n_neighbors: Number of neighbors to use for imputation
        """
        self.min_similarity = min_similarity
        self.n_neighbors = n_neighbors
        self.scaler = StandardScaler()
        self.districts: dict[str, DistrictMetadata] = {}
        self.feature_weights = {
            # Higher weights for more important features
            'median_income': 1.5,
            'poverty_rate': 1.2,
            'population_density': 1.0,
            'median_age': 1.0,
            'household_size': 1.0,
            'homeownership_rate': 0.8,
            'urban_percentage': 0.8
        }
        
    def add_district(self, district_id: str, features: dict[str, float], has_soi_data: bool = True) -> None:
        """Add a district to the imputation dataset."""
        self.districts[district_id] = DistrictMetadata(
            district_id=district_id,
            has_soi_data=has_soi_data,
            features={},  # Will be set in fit()
            raw_data=features
        )
    
    def fit(self) -> None:
        """Fit the imputer to the district data."""
        if not self.districts:
            raise ValueError("No districts added to imputer")
            
        # Create feature matrix
        feature_names = list(next(iter(self.districts.values())).raw_data.keys())
        X = np.array([
            [d.raw_data[f] for f in feature_names] 
            for d in self.districts.values()
        ])
        
        # Scale features
        X_scaled = self.scaler.fit_transform(X)
        
        # Apply feature weights
        weights = np.array([self.feature_weights.get(f, 1.0) for f in feature_names])
        X_weighted = X_scaled * weights
        
        # Store scaled features
        for (_district_id, district), features in zip(self.districts.items(), X_weighted, strict=False):
            district.features = dict(zip(feature_names, features, strict=False))
    
    def find_nearest_neighbors(self, target_district_id: str) -> list[tuple[str, float]]:
        """Find nearest neighbors for a target district."""
        if target_district_id not in self.districts:
            raise ValueError(f"District {target_district_id} not found in imputer")
            
        target = self.districts[target_district_id]
        candidates = [
            d for d in self.districts.values() 
            if d.has_soi_data and d.district_id != target_district_id
        ]
        
        if not candidates:
            return []
            
        # Calculate distances (inverse of similarity)
        target_vec = target.feature_vector.reshape(1, -1)
        candidate_vecs = np.array([d.feature_vector for d in candidates])
        
        # Use Euclidean distance (smaller is more similar)
        distances = euclidean_distances(target_vec, candidate_vecs)[0]
        
        # Convert to similarity scores (1 / (1 + distance))
        similarities = 1 / (1 + distances)
        
        # Filter and sort results
        results = [
            (candidates[i].district_id, similarities[i])
            for i in np.argsort(similarities)[::-1]
            if similarities[i] >= self.min_similarity
        ]
        
        return results[:self.n_neighbors]
    
    def impute_district(self, target_district_id: str, soi_data: dict[str, dict]) -> dict:
        """
        Impute SOI data for a target district using nearest neighbors.
        
        Args:
            target_district_id: ID of the district to impute
            soi_data: Dictionary mapping district IDs to their SOI data
            
        Returns:
            Dictionary with imputed SOI data and metadata
        """
        if target_district_id not in self.districts:
            raise ValueError(f"District {target_district_id} not found in imputer")
            
        # Find nearest neighbors with SOI data
        neighbors = self.find_nearest_neighbors(target_district_id)
        
        if not neighbors:
            logger.warning(
                f"No similar districts found for {target_district_id} with similarity >= {self.min_similarity}"
            )
            return {
                'imputation_method': 'fallback',
                'confidence_score': 0,
                'source_districts': [],
                'similarity_scores': [],
                'fallback_used': True
            }
        
        # Get SOI data for neighbors
        neighbor_ids = [n[0] for n in neighbors]
        neighbor_scores = [n[1] for n in neighbors]
        neighbor_soi = [soi_data[nid] for nid in neighbor_ids if nid in soi_data]
        
        if not neighbor_soi:
            logger.warning(f"No SOI data found for any neighbors of {target_district_id}")
            return {
                'imputation_method': 'fallback',
                'confidence_score': 0,
                'source_districts': [],
                'similarity_scores': [],
                'fallback_used': True
            }
        
        # Calculate weighted average of neighbor SOI data
        weights = np.array(neighbor_scores[:len(neighbor_soi)])
        weights = weights / weights.sum()  # Normalize to sum to 1
        
        # Impute each SOI metric
        result = {}
        for metric in neighbor_soi[0]:
            if metric == 'metadata':
                continue
                
            if isinstance(neighbor_soi[0][metric], (int, float)):
                # Simple weighted average for numeric values
                values = np.array([n[metric] for n in neighbor_soi])
                result[metric] = float(np.sum(values * weights))
            
        # Calculate confidence score (0-100)
        confidence = min(100, int(np.mean(neighbor_scores[:len(neighbor_soi)]) * 100))
        
        return {
            **result,
            'metadata': {
                'imputation_method': 'nearest_neighbor',
                'confidence_score': confidence,
                'source_districts': neighbor_ids[:len(neighbor_soi)],
                'similarity_scores': neighbor_scores[:len(neighbor_soi)].tolist(),
                'fallback_used': False
            }
        }

def impute_missing_districts(
    districts_df: pd.DataFrame,
    soi_data: dict[str, dict],
    feature_columns: list[str],
    min_similarity: float = 0.7,
    n_neighbors: int = 3
) -> dict[str, dict]:
    """
    Impute SOI data for districts without direct data using nearest neighbors.
    
    Args:
        districts_df: DataFrame with district features (must include 'district_id' column)
        soi_data: Dictionary mapping district IDs to their SOI data
        feature_columns: List of column names to use for similarity calculation
        min_similarity: Minimum similarity score (0-1) to consider a neighbor
        n_neighbors: Number of neighbors to use for imputation
        
    Returns:
        Dictionary mapping district IDs to their imputed SOI data
    """
    # Initialize imputer
    imputer = DistrictNearestNeighborImputer(
        min_similarity=min_similarity,
        n_neighbors=n_neighbors
    )
    
    # Add districts to imputer
    for _, row in districts_df.iterrows():
        district_id = row['district_id']
        features = {col: row[col] for col in feature_columns if col in row}
        has_data = district_id in soi_data
        imputer.add_district(district_id, features, has_soi_data=has_data)
    
    # Fit the imputer
    imputer.fit()
    
    # Impute missing districts
    results = {}
    for district_id in imputer.districts:
        if district_id in soi_data:
            # Use original data if available
            results[district_id] = soi_data[district_id]
        else:
            # Impute missing data
            results[district_id] = imputer.impute_district(district_id, soi_data)
    
    return results
