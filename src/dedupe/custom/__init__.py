"""A project's own fields: matching on columns the built-in customer schema does not know (company names, product codes, any number or date)."""
from .fields import KINDS, BlockingRule, CustomField
from .spec import build_spec, spec_of_run

__all__ = ["KINDS", "BlockingRule", "CustomField", "build_spec", "spec_of_run"]
