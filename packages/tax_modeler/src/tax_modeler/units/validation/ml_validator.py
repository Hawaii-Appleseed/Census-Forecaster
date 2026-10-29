"""
ML-based validation for tax unit classification.

This module provides machine learning-based validation to identify potential
misclassifications in tax unit filing statuses (Single, HoH, Joint).
"""

from __future__ import annotations

import pandas as pd
import numpy as np
from typing import List, Dict, Any, Optional, Union
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

# Constants for validation rules
MIN_INCOME_HOH = 10000  # Minimum income to qualify as HoH
MAX_INCOME_HOH = 200000  # Income above which HoH is less likely
MIN_AGE_HOH = 19  # Minimum age to file as HoH
MAX_AGE_HOH = 65  # Age after which HoH status might change
MIN_DEPENDENTS_HOH = 1  # Minimum dependents for HoH

class MLTaxUnitValidator:
    """Machine learning validator for tax unit classification.
    
    This class provides methods to validate tax unit classifications using
    machine learning models to identify potential misclassifications.
    """
    
    def __init__(self, model_path: Optional[str] = None):
        """Initialize the ML validator.
        
        Args:
            model_path: Path to a pre-trained model. If None, uses rule-based validation.
        """
        self.model = None
        self.features = [
            'income',
            'num_dependents',
            'age',
            'has_children',
            'is_householder',
            'housing_cost_ratio',
            'marital_status_single',
            'marital_status_married',
            'marital_status_divorced',
            'marital_status_widowed'
        ]
        self.target = 'filing_status'
        
        # Load model if path is provided
        if model_path and Path(model_path).exists():
            try:
                # Imported here, not at module level: joblib and the
                # scikit-learn model it unpickles take ~1.4 s to import, and
                # the constructor imports this module whether or not it is
                # given a model.
                import joblib
                self.model = joblib.load(model_path)
                logger.info(f"Loaded ML model from {model_path}")
            except Exception as e:
                logger.error(f"Failed to load model from {model_path}: {e}")
                self.model = None
        
    def extract_features(self, tax_units: List[Dict[str, Any]]) -> pd.DataFrame:
        """Extract features from tax units for ML validation.
        
        Args:
            tax_units: List of tax unit dictionaries
            
        Returns:
            DataFrame with extracted features
        """
        # Calculate derived features
        features = {}
        
        # Basic features
        features['income'] = [float(unit.get('income', 0)) for unit in tax_units]
        features['num_dependents'] = [int(unit.get('num_dependents', 0)) for unit in tax_units]
        
        # Extract age from primary_filer if available
        ages = []
        for unit in tax_units:
            primary_filer = unit.get('primary_filer', {})
            if isinstance(primary_filer, dict):
                age = primary_filer.get('age')
                if age is None or age == 0:
                    # Use income-based age estimation if age is missing
                    income = float(unit.get('income', 0))
                    if income < 25000:
                        age = 25  # Young worker
                    elif income < 50000:
                        age = 35  # Mid-career
                    elif income < 100000:
                        age = 45  # Experienced worker
                    else:
                        age = 50  # Senior worker
                else:
                    age = int(age)
            else:
                age = 35  # Default age
            ages.append(age)
        features['age'] = ages
        
        # Boolean features
        features['has_children'] = [1 if deps > 0 else 0 for deps in features['num_dependents']]
        features['is_householder'] = [int(unit.get('is_householder', 1)) for unit in tax_units]
        
        # Financial ratios - use default housing cost ratio if not available
        housing_ratios = []
        for unit in tax_units:
            income = float(unit.get('income', 1))
            housing_costs = float(unit.get('housing_costs', income * 0.3))  # Default 30% of income
            ratio = housing_costs / max(income, 1) if income > 0 else 0.3
            housing_ratios.append(min(ratio, 1.0))  # Cap at 100%
        features['housing_cost_ratio'] = housing_ratios
        
        # Categorical features - one-hot encode marital status
        marital_statuses = []
        for unit in tax_units:
            primary_filer = unit.get('primary_filer', {})
            if isinstance(primary_filer, dict):
                marital_status = primary_filer.get('marital_status', 'single')
            else:
                marital_status = 'single'
            marital_statuses.append(marital_status)
        
        for status in ['single', 'married', 'divorced', 'widowed']:
            features[f'marital_status_{status}'] = [1 if ms == status else 0 for ms in marital_statuses]
        
        # Create final feature DataFrame with consistent column order
        feature_df = pd.DataFrame(features)
        
        # Ensure all expected columns are present and in correct order
        for col in self.features:
            if col not in feature_df.columns:
                feature_df[col] = 0
                
        return feature_df[self.features]  # Ensure consistent column order
    
    def predict(self, tax_units: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Predict potential misclassifications.
        
        Args:
            tax_units: List of tax units to validate
            
        Returns:
            List of tax units with validation flags
        """
        if not tax_units:
            return []
            
        # Extract features
        features = self.extract_features(tax_units)
        
        # If no model is loaded, use rule-based validation
        if self.model is None:
            logger.warning("No ML model loaded. Using rule-based validation.")
            return self._rule_based_validation(tax_units, features)
            
        # Use ML model for prediction
        try:
            # Get predicted probabilities for each class
            probas = self.model.predict_proba(features)
            predicted_classes = self.model.classes_
            
            logger.info(f"ML validation processing {len(tax_units)} tax units")
            logger.debug(f"Model classes: {predicted_classes}")
            
            # For each tax unit, get the predicted class and confidence
            validated_units = []
            ml_flags_count = 0
            rule_flags_count = 0
            
            for i, unit in enumerate(tax_units):
                unit = unit.copy()
                current_status = str(unit.get('filing_status', '')).lower()
                
                # Get prediction with highest probability
                max_idx = np.argmax(probas[i])
                predicted_status = str(predicted_classes[max_idx]).lower()
                confidence = float(probas[i][max_idx])
                
                logger.debug(f"Unit {i+1}: {current_status} -> {predicted_status} (confidence: {confidence:.1%})")
                
                # Normalize status names for comparison
                current_normalized = current_status.replace('married_filing_jointly', 'joint').replace('head_of_household', 'hoh')
                
                # Only flag if prediction is different from current status
                # and confidence is above threshold (lowered from 0.7 to 0.6)
                if (predicted_status != current_normalized and 
                    confidence > 0.6 and 
                    predicted_status in ['single', 'hoh', 'joint']):
                    
                    unit.setdefault('validation_flags', []).append({
                        'code': f'PREDICTED_{predicted_status.upper()}',
                        'message': (
                            f'Model predicts this should be {predicted_status.upper()} '
                            f'instead of {current_status.upper()}. '
                            f'(Confidence: {confidence:.1%})'
                        ),
                        'confidence': confidence,
                        'suggested_status': predicted_status
                    })
                    ml_flags_count += 1
                    logger.info(f"ML flag added for unit {i+1}: {current_status} -> {predicted_status}")
                
                # Add rule-based validations as well
                unit_before_rules = len(unit.get('validation_flags', []))
                unit = self._apply_rule_based_validation(unit, features.iloc[i])
                unit_after_rules = len(unit.get('validation_flags', []))
                
                if unit_after_rules > unit_before_rules:
                    rule_flags_count += unit_after_rules - unit_before_rules
                    logger.debug(f"Rule-based flags added for unit {i+1}: {unit_after_rules - unit_before_rules}")
                
                validated_units.append(unit)
            
            logger.info(f"ML validation complete: {ml_flags_count} ML flags, {rule_flags_count} rule-based flags")
                
            return validated_units
            
        except Exception as e:
            logger.error(f"Error in ML prediction: {e}")
            logger.warning("Falling back to rule-based validation")
            return self._rule_based_validation(tax_units, features)
    
    def _rule_based_validation(self, 
                             tax_units: List[Dict[str, Any]], 
                             features: pd.DataFrame) -> List[Dict[str, Any]]:
        """Apply rule-based validation to all tax units."""
        return [
            self._apply_rule_based_validation(unit, features.iloc[i])
            for i, unit in enumerate(tax_units)
        ]
    
    def _apply_rule_based_validation(self, 
                                   unit: Dict[str, Any], 
                                   features: pd.Series) -> Dict[str, Any]:
        """Apply rule-based validation to a single tax unit."""
        unit = unit.copy()
        validation_flags = []
        
        # Extract basic info
        filing_status = str(unit.get('filing_status', '')).lower()
        income = float(features.get('income', 0))
        num_dependents = int(features.get('num_dependents', 0))
        age = int(features.get('age', 0))
        is_householder = bool(features.get('is_householder', 0))
        housing_ratio = float(features.get('housing_cost_ratio', 0))
        
        # Rule 1: Single with dependents might qualify as HoH
        if (filing_status == 'single' and 
            num_dependents >= MIN_DEPENDENTS_HOH and
            income >= MIN_INCOME_HOH and
            MIN_AGE_HOH <= age <= MAX_AGE_HOH and
            is_householder):
            
            confidence = min(0.7 + (num_dependents * 0.1), 0.9)
            validation_flags.append({
                'code': 'POTENTIAL_HOH',
                'message': (
                    f'Single filer with {num_dependents} dependents might qualify as Head of Household. '
                    f'Verify if they meet all HoH requirements.'
                ),
                'confidence': confidence,
                'suggested_status': 'hoh'
            })
        
        # Rule 2: High income HoH might be incorrect
        if (filing_status == 'hoh' and 
            income > MAX_INCOME_HOH and
            num_dependents == 1 and
            age > 50):
            
            validation_flags.append({
                'code': 'HIGH_INCOME_HOH',
                'message': (
                    f'High income Head of Household (${income:,.2f}) with only one dependent. '
                    f'Verify if this filing status is correct.'
                ),
                'confidence': 0.8,
                'suggested_status': 'single' if num_dependents == 0 else 'joint'
            })
        
        # Rule 3: Married filing single might be incorrect
        if (filing_status == 'single' and
            features.get('marital_status_married', 0) == 1 and
            num_dependents > 0):
            
            validation_flags.append({
                'code': 'MARRIED_FILING_SINGLE',
                'message': 'Married taxpayer filing as single with dependents. Verify if should file jointly.',
                'confidence': 0.85,
                'suggested_status': 'joint'
            })
        
        # Rule 4: Low housing cost ratio for HoH
        if (filing_status == 'hoh' and
            housing_ratio < 0.1 and
            income > 50000):
            
            validation_flags.append({
                'code': 'LOW_HOUSING_RATIO',
                'message': (
                    f'Low housing cost ratio ({housing_ratio:.1%}) for HoH. '
                    f'Verify if they pay more than half the household costs.'
                ),
                'confidence': 0.7,
                'suggested_status': 'single'
            })
        
        # Add validation flags to unit if any were found
        if validation_flags:
            unit['validation_flags'] = validation_flags
            
        return unit
    
    def save_model(self, path: str):
        """Save the trained model to disk."""
        # TODO: Implement model saving
        pass
        
    def load_model(self, path: str):
        """Load a trained model from disk."""
        # TODO: Implement model loading
        pass


def validate_tax_units(tax_units: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convenience function to validate tax units using the ML validator.
    
    Args:
        tax_units: List of tax units to validate
        
    Returns:
        List of tax units with validation flags
    """
    validator = MLTaxUnitValidator()
    return validator.predict(tax_units)
