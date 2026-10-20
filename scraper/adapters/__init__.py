"""Adapter registry: platform name -> adapter class.

pageup, cornerstone, adp, avature, dayforce, greenhouse, lever, smartrecruiters, brassring, paycom and
custom are not implemented specifically; detect.PLATFORM_MAP routes them to GenericAdapter
(requests, then Playwright).
"""
from __future__ import annotations

from .careersite import CareerSiteAdapter
from .eightfold import EightfoldAdapter
from .generic import GenericAdapter
from .hopkins import HopkinsAdapter
from .icims import IcimsAdapter
from .jibe import JibeAdapter
from .oracle_cloud import OracleCloudAdapter
from .phenom import PhenomAdapter
from .successfactors import SuccessFactorsAdapter
from .symphony import SymphonyAdapter
from .talentbrew import TalentBrewAdapter
from .taleo import TaleoAdapter
from .ultipro import UltiproAdapter
from .workday import WorkdayAdapter

REGISTRY = {
    "workday": WorkdayAdapter,
    "icims": IcimsAdapter,
    "jibe": JibeAdapter,
    "talentbrew": TalentBrewAdapter,
    "oracle_cloud": OracleCloudAdapter,
    "successfactors": SuccessFactorsAdapter,
    "phenom": PhenomAdapter,
    "ultipro": UltiproAdapter,
    "eightfold": EightfoldAdapter,
    "taleo": TaleoAdapter,
    "symphony": SymphonyAdapter,
    "careersite": CareerSiteAdapter,
    "hopkins": HopkinsAdapter,
    "generic": GenericAdapter,
}


def get_adapter(platform: str):
    return REGISTRY.get(platform, GenericAdapter)
