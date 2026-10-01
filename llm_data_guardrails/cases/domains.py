"""Product domains used to vary the surface of each case.

Every trap is rendered in a randomly chosen domain so a model cannot pass by recognizing
a textbook example. The statistics are identical across domains; only names change.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, eq=False)
class Domain:
    key: str
    product: str
    unit: str  # denominator column, e.g. "sessions"
    users: str  # how people are referred to, e.g. "shoppers"
    conversion: str  # human label for success / unit
    success: str  # numerator column, e.g. "orders"
    features: tuple[str, ...]
    dims: dict[str, tuple[str, ...]] = field(default_factory=dict)
    mid_event: str = ""  # client-side funnel event column
    mid_label: str = ""
    mid_verb: str = ""
    filter_change: str = ""  # a pipeline change that removes junk from the denominator


CHANNELS = ("Paid search", "Paid social", "Organic search", "Email", "Affiliates", "Referral")

DOMAINS: tuple[Domain, ...] = (
    Domain(
        key="ecommerce",
        product="online store",
        unit="sessions",
        users="shoppers",
        conversion="checkout conversion",
        success="orders",
        features=("one-click checkout", "redesigned product page", "saved carts", "free-shipping banner"),
        dims={
            "platform": ("iOS", "Android", "Web"),
            "channel": CHANNELS,
            "region": ("US", "Canada", "UK", "Germany", "Brazil", "Australia"),
        },
        mid_event="add_to_cart_events",
        mid_label="add-to-cart",
        mid_verb="adding items to their carts",
        filter_change="Bot-traffic filter enabled in the session pipeline",
    ),
    Domain(
        key="saas",
        product="B2B analytics platform",
        unit="trials",
        users="trial accounts",
        conversion="trial-to-paid conversion",
        success="paid_conversions",
        features=("guided setup", "template gallery", "in-app chat", "new pricing page"),
        dims={
            "platform": ("Web app", "Desktop app"),
            "plan": ("Starter", "Team", "Business"),
            "channel": CHANNELS,
            "region": ("North America", "EMEA", "APAC", "LATAM"),
        },
        mid_event="dashboard_created_events",
        mid_label="dashboard-creation",
        mid_verb="creating dashboards",
        filter_change="Spam-signup filter enabled for new trials",
    ),
    Domain(
        key="fintech",
        product="mobile banking app",
        unit="applications",
        users="applicants",
        conversion="application-to-funded rate",
        success="funded_accounts",
        features=("instant ID verification", "referral bonus", "simplified onboarding", "card-first signup"),
        dims={
            "platform": ("iOS", "Android"),
            "channel": CHANNELS,
            "region": ("Northeast", "Southeast", "Midwest", "Southwest", "West"),
        },
        mid_event="id_check_started_events",
        mid_label="ID-check start",
        mid_verb="starting ID checks",
        filter_change="Duplicate-application filter enabled",
    ),
    Domain(
        key="delivery",
        product="food delivery app",
        unit="app_sessions",
        users="customers",
        conversion="order rate",
        success="orders",
        features=("reorder button", "new search ranking", "delivery-fee cap", "restaurant ratings redesign"),
        dims={
            "platform": ("iOS", "Android", "Web"),
            "channel": CHANNELS,
            "region": ("San Francisco", "Chicago", "Austin", "Seattle", "Atlanta", "Denver"),
        },
        mid_event="add_to_cart_events",
        mid_label="add-to-cart",
        mid_verb="adding items to their carts",
        filter_change="Bot-traffic filter enabled in the session pipeline",
    ),
    Domain(
        key="streaming",
        product="video streaming service",
        unit="trial_starts",
        users="trial users",
        conversion="trial-to-subscription rate",
        success="subscriptions",
        features=("personalized home row", "autoplay previews", "annual plan offer", "offline downloads"),
        dims={
            "platform": ("Smart TV", "iOS", "Android", "Web"),
            "channel": CHANNELS,
            "region": ("US", "UK", "Mexico", "India", "Japan"),
        },
        mid_event="title_played_events",
        mid_label="title-play",
        mid_verb="playing titles",
        filter_change="Duplicate-trial filter enabled",
    ),
    Domain(
        key="edtech",
        product="online learning platform",
        unit="visits",
        users="learners",
        conversion="course enrollment rate",
        success="enrollments",
        features=("course preview videos", "skill quiz onboarding", "bundle pricing", "mentor chat"),
        dims={
            "platform": ("Web", "iOS", "Android"),
            "channel": CHANNELS,
            "region": ("US", "India", "UK", "Nigeria", "Philippines"),
        },
        mid_event="lesson_started_events",
        mid_label="lesson-start",
        mid_verb="starting lessons",
        filter_change="Bot-traffic filter enabled in the visit pipeline",
    ),
)

DOMAINS_BY_KEY = {d.key: d for d in DOMAINS}
