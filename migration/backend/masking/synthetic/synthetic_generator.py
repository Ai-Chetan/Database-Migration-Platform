"""
Synthetic Data Generator
File: migration/backend/masking/synthetic/synthetic_generator.py

Generates deterministic fake data for lower-environment migrations.
"Deterministic" means the same source row always produces the same fake
value — this preserves referential integrity across tables:

    customers.name = "John Doe"  → always becomes "Alice Smith"
    orders.customer_id = 42      → customer 42 is always "Alice Smith"
    Both tables generate the same fake name for the same customer.

Uses the `faker` library (pip install faker).
Seed is derived from the source column value, ensuring determinism.

Supported generators:
    fake_name         → "Alice Smith"
    fake_first_name   → "Alice"
    fake_last_name    → "Smith"
    fake_email        → "alice.smith@example.com"
    fake_phone        → "+1-555-867-5309"
    fake_address      → "123 Main St, Springfield, IL 62701"
    fake_city         → "Springfield"
    fake_postcode     → "62701"
    fake_country      → "United States"
    fake_company      → "Acme Corporation"
    fake_ssn          → "123-45-6789"
    fake_credit_card  → "4111111111111111"
    fake_date         → "1985-06-15"
    fake_dob          → "1985-06-15" (date of birth, 18-80 years ago)
    fake_username     → "alice_smith_42"
    fake_ipv4         → "192.168.1.1"
    fake_url          → "https://example.com/path"
    fake_text         → "Lorem ipsum..."
    fake_integer      → random integer in configured range

Usage:
    gen = SyntheticGenerator(locale="en_US")
    email = gen.generate("fake_email", seed_value="john@prod.com")
    # → always same fake email for "john@prod.com"

mapping_config for synthesize kind:
    {"generator": "fake_email", "locale": "en_US", "seed_column": "id"}
"""

import hashlib
from typing import Any, Dict, Optional


class SyntheticGenerator:

    def __init__(self, locale: str = "en_US"):
        self.locale = locale
        self._faker_cache: Dict[str, Any] = {}

    def generate(
        self,
        generator:  str,
        seed_value: Any = None,
        config:     Dict = None,
    ) -> Any:
        """
        Generate a synthetic value deterministically.
        Same seed_value always produces same output.
        """
        config = config or {}

        # Compute integer seed from source value for determinism
        seed = self._compute_seed(seed_value)

        faker = self._get_faker(self.locale, seed)

        return self._dispatch(faker, generator, seed, config)

    def generate_row(
        self,
        row:          Dict[str, Any],
        column_rules: Dict[str, Dict],
        # {col_name: {"generator": "fake_email", "seed_column": "id", "locale": "en_US"}}
    ) -> Dict[str, Any]:
        """
        Apply synthetic data generation to an entire row.
        Returns a new row dict with synthetic values in specified columns.
        Preserves all other columns unchanged.
        """
        result = dict(row)

        for col_name, rule in column_rules.items():
            if col_name not in row:
                continue

            generator   = rule.get("generator", "fake_text")
            seed_col    = rule.get("seed_column", col_name)
            locale      = rule.get("locale", self.locale)
            seed_value  = row.get(seed_col, row.get(col_name))

            try:
                gen = SyntheticGenerator(locale=locale)
                result[col_name] = gen.generate(generator, seed_value, rule)
            except Exception:
                result[col_name] = None

        return result

    # ── Private ───────────────────────────────────────────────────────────────

    def _compute_seed(self, value: Any) -> int:
        """Convert any value to a deterministic integer seed."""
        if value is None:
            return 42
        s     = str(value)
        h     = hashlib.md5(s.encode()).hexdigest()[:8]
        return int(h, 16) % (2**31)

    def _get_faker(self, locale: str, seed: int):
        """Get a seeded Faker instance. Cached by locale only (re-seeded per call)."""
        try:
            from faker import Faker
        except ImportError:
            raise ImportError(
                "faker package required for synthetic data generation. "
                "Install with: pip install faker"
            )

        cache_key = locale
        if cache_key not in self._faker_cache:
            self._faker_cache[cache_key] = Faker(locale)

        f = self._faker_cache[cache_key]
        Faker.seed(seed)
        return f

    def _dispatch(self, faker, generator: str, seed: int, config: Dict) -> Any:
        """Call the right faker method based on generator name."""
        gen = generator.lower()

        dispatch = {
            "fake_name":        lambda: faker.name(),
            "fake_first_name":  lambda: faker.first_name(),
            "fake_last_name":   lambda: faker.last_name(),
            "fake_email":       lambda: faker.email(),
            "fake_phone":       lambda: faker.phone_number(),
            "fake_address":     lambda: faker.address().replace("\n", ", "),
            "fake_city":        lambda: faker.city(),
            "fake_postcode":    lambda: faker.postcode(),
            "fake_country":     lambda: faker.country(),
            "fake_company":     lambda: faker.company(),
            "fake_ssn":         lambda: faker.ssn(),
            "fake_credit_card": lambda: faker.credit_card_number(card_type=config.get("card_type")),
            "fake_date":        lambda: str(faker.date_between(
                                    start_date=config.get("start_date", "-10y"),
                                    end_date=config.get("end_date", "today")
                                )),
            "fake_dob":         lambda: str(faker.date_of_birth(
                                    minimum_age=config.get("min_age", 18),
                                    maximum_age=config.get("max_age", 80)
                                )),
            "fake_username":    lambda: faker.user_name(),
            "fake_ipv4":        lambda: faker.ipv4(),
            "fake_url":         lambda: faker.url(),
            "fake_text":        lambda: faker.text(max_nb_chars=config.get("max_length", 200)),
            "fake_sentence":    lambda: faker.sentence(),
            "fake_integer":     lambda: faker.random_int(
                                    min=config.get("min", 1),
                                    max=config.get("max", 1000000)
                                ),
            "fake_uuid":        lambda: str(faker.uuid4()),
            "fake_iban":        lambda: faker.iban(),
            "fake_license":     lambda: faker.license_plate(),
            "fake_color":       lambda: faker.color_name(),
            "fake_job":         lambda: faker.job(),
        }

        fn = dispatch.get(gen)
        if fn is None:
            # Unknown generator — return a hash of the seed
            return f"SYNTHETIC_{seed:08x}"

        try:
            return fn()
        except Exception:
            return f"SYNTHETIC_{seed:08x}"
