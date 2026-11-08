import pytest
from pydantic import ValidationError

from app.models.schemas import AttributeSpec, ClassificationResult, ExtractedListing, ReviewDecision
from app.services import rules
from app.services.workflow import list_categories


def cat(cid):
    return next(c for c in list_categories() if c["category_id"] == cid)


def test_extracted_listing_rejects_hallucinated_keys():
    with pytest.raises(ValidationError):
        ExtractedListing.model_validate({"product_type": "Shorts", "is_compliant": True})


def test_extracted_listing_bounds():
    with pytest.raises(ValidationError):
        ExtractedListing.model_validate({"product_type": "Shorts", "pack_count": 0})
    with pytest.raises(ValidationError):
        ExtractedListing.model_validate({"product_type": "Shorts", "gender": "Male"})


def test_classification_confidence_bounds():
    with pytest.raises(ValidationError):
        ClassificationResult.model_validate({"category_id": "X1", "confidence": 1.4, "rationale": "x"})


def test_attribute_spec_strict():
    with pytest.raises(ValidationError):
        AttributeSpec.model_validate({"name": "size", "type": "float"})


def test_missing_required_attribute():
    v = rules.validate_attributes({"colors": ["Black"], "gender": "Men"}, cat("APP-SHORTS-M"))
    assert [(x.code, x.field) for x in v] == [("MISSING_REQUIRED_ATTR", "size")]


def test_invalid_enum_value():
    v = rules.validate_attributes({"size": ["L"], "colors": ["Black"], "gender": "Women"}, cat("APP-SHORTS-M"))
    assert v[0].code == "INVALID_ATTR_VALUE" and v[0].field == "gender"


def test_int_range():
    v = rules.validate_attributes({"connector_type": "USB-C", "wattage": 999}, cat("ELEC-CHRG"))
    assert v[0].code == "INVALID_ATTR_VALUE" and v[0].field == "wattage"


def test_valid_attributes_pass():
    assert rules.validate_attributes({"connector_type": "USB-C", "wattage": 20}, cat("ELEC-CHRG")) == []


def test_prohibited_keywords_global_and_category():
    v = rules.check_prohibited({"raw_title": "Ashwagandha FDA Approved"}, cat("HLTH-SUPP"))
    assert v and v[0].code == "PROHIBITED_KEYWORD" and "fda approved" in v[0].detail
    v = rules.check_prohibited({"raw_title": "Replica jersey"}, None)
    assert v and "global" in v[0].detail


def test_prohibited_word_boundary():
    # 'cure' must not match 'secure' or 'pedicure'
    assert rules.check_prohibited({"raw_title": "Secure pedicure kit"}, cat("HLTH-SUPP")) == []


def test_price_and_routing():
    assert rules.check_price(0)[0].code == "INVALID_PRICE"
    assert rules.check_price(None)[0].code == "INVALID_PRICE"
    assert rules.check_price(19.99) == []
    warn = rules.check_title("Men's Running Shorts", "MENS RUNNING SHORTS QUICK DRY", False)
    assert warn and rules.route(warn) == "DRAFT"  # WARN only does not block


def test_review_decision_validation():
    with pytest.raises(ValidationError):
        ReviewDecision(listing_id=1, decision="APPROVE", reviewer="x")  # too short
    with pytest.raises(ValidationError):
        ReviewDecision(listing_id=1, decision="MAYBE", reviewer="alice")
