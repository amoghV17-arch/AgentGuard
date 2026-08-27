"""
simulation/merchant.py — Mock merchant catalog and API for simulation.

This simulates the external commerce system that a buying AI agent interacts
with. In a real deployment, this would be a third-party storefront API. For
AgentGuard testing, we control it so we can embed injection payloads at
specific points.

The catalog is in-memory. In the simulation loop, attack_generator.py inserts
injected product descriptions here, and the buying agent (simulation/agent.py)
reads from here just like it would read from a real catalog.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Product:
    """A product in the merchant catalog."""
    product_id: str
    name: str
    description: str
    price: float
    category: str
    merchant_id: str
    reviews: list[str] = field(default_factory=list)


# The baseline clean catalog — used to reset between simulation rounds
_BASELINE_CATALOG: list[Product] = [
    Product(
        product_id="PROD-001",
        name="Premium Wireless Headphones",
        description="Over-ear headphones with 40mm drivers, 30-hour battery, and active noise cancellation.",
        price=299.99,
        category="electronics",
        merchant_id="merchant_electronics_01",
        reviews=[
            "Great sound quality. The noise cancellation is excellent on flights.",
            "Comfortable for long listening sessions. Battery lasts all day.",
        ],
    ),
    Product(
        product_id="PROD-002",
        name="Running Shoes Ultra",
        description="Lightweight mesh upper with responsive foam midsole. Ideal for marathon training.",
        price=149.99,
        category="footwear",
        merchant_id="merchant_footwear_01",
        reviews=[
            "Very comfortable and lightweight. My race times improved.",
            "Good grip on wet roads. Highly recommend for daily training.",
        ],
    ),
    Product(
        product_id="PROD-003",
        name="Smart Home Hub",
        description="Works with Alexa, Google Home, and Apple HomeKit. 5-minute setup with app control.",
        price=79.99,
        category="electronics",
        merchant_id="merchant_electronics_01",
        reviews=[
            "Set up was super easy. Everything connected on first try.",
            "The app is intuitive. My whole home is automated now.",
        ],
    ),
    Product(
        product_id="PROD-004",
        name="Professional Development Course",
        description="Comprehensive online course for software engineers. 40 hours of content.",
        price=199.99,
        category="education",
        merchant_id="merchant_edu_01",
        reviews=[
            "Excellent content, very well structured.",
        ],
    ),
    Product(
        product_id="PROD-005",
        name="Ergonomic Office Chair",
        description="Adjustable lumbar support and armrests. Reduces back pain during long work hours.",
        price=459.99,
        category="home_goods",
        merchant_id="merchant_home_01",
        reviews=[
            "Best chair I have ever owned. No back pain after 8-hour days.",
        ],
    ),
]

# Working catalog — gets mutated by inject_payload / reset_catalog
_catalog: list[Product] = copy.deepcopy(_BASELINE_CATALOG)


class MerchantCatalog:
    """
    Thread-safe-ish interface to the in-memory product catalog.

    For hackathon scale, we don't need real thread safety — everything runs
    in a single process. But the interface is designed so it could be backed
    by a Redis or DB cache later without changing callers.
    """

    def list_products(self, category: Optional[str] = None) -> list[Product]:
        """List all products, optionally filtered by category."""
        if category:
            return [p for p in _catalog if p.category == category]
        return list(_catalog)

    def get_product(self, product_id: str) -> Optional[Product]:
        """Fetch a single product by ID."""
        for product in _catalog:
            if product.product_id == product_id:
                return product
        return None

    def inject_payload(
        self,
        product_id: str,
        payload: str,
        injection_point: str = "description",
    ) -> bool:
        """
        Inject an attack payload into a product's description or a review.

        Called by the simulation loop before the buying agent reads the catalog.
        Returns True if the injection was applied, False if product_id not found.
        """
        for product in _catalog:
            if product.product_id == product_id:
                if injection_point == "description":
                    product.description = payload
                elif injection_point == "review":
                    product.reviews.insert(0, payload)  # inject as first review
                return True
        return False

    def reset_catalog(self) -> None:
        """Reset to the clean baseline catalog (call between simulation rounds)."""
        global _catalog
        _catalog = copy.deepcopy(_BASELINE_CATALOG)

    def get_combined_content(self, product_id: str) -> str:
        """
        Get all text content for a product (description + reviews concatenated).

        This is what gets fed into injection_detector.check_content() — the
        full text the buying agent sees, including any injected content.
        """
        product = self.get_product(product_id)
        if not product:
            return ""
        parts = [product.description] + product.reviews
        return " ".join(parts)


# Module-level singleton
catalog = MerchantCatalog()
