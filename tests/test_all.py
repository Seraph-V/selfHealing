"""
Test suite for the self-healing pipeline thesis.

These tests define what "green" looks like for each scenario category:
  - test_functional_*      → Functional regression scenarios
  - test_syntactic_*       → Caught by flake8 in the lint job (not pytest)
  - test_configurational_* → Dependency / import availability
  - test_architectural_*   → Circular import / structural integrity
"""
import pytest


# ── Functional Regression Tests ───────────────────────────────────────────────

class TestFunctional:
    """
    These tests will FAIL when src/calculator.py has wrong operators
    and PASS after the agent applies the correct fix.
    """

    def test_add_positive(self):
        from src.calculator import add
        assert add(2, 3) == 5

    def test_add_negative(self):
        from src.calculator import add
        assert add(-1, -1) == -2

    def test_add_zero(self):
        from src.calculator import add
        assert add(0, 5) == 5

    def test_subtract(self):
        from src.calculator import subtract
        assert subtract(10, 4) == 6

    def test_subtract_negative_result(self):
        from src.calculator import subtract
        assert subtract(3, 7) == -4

    def test_multiply(self):
        from src.calculator import multiply
        assert multiply(3, 4) == 12

    def test_multiply_by_zero(self):
        from src.calculator import multiply
        assert multiply(5, 0) == 0

    def test_divide(self):
        from src.calculator import divide
        assert divide(10, 2) == 5.0

    def test_divide_by_zero_raises(self):
        from src.calculator import divide
        with pytest.raises(ValueError, match="Division by zero"):
            divide(5, 0)


# ── Configurational Regression Tests ─────────────────────────────────────────

class TestConfigurational:
    """
    These tests verify that required packages are importable.
    They FAIL if requirements.txt pins a nonexistent version and
    pip install fails during the CI setup step.
    """

    def test_requests_importable(self):
        import requests  # noqa: F401
        assert requests.__version__

    def test_pytest_importable(self):
        import pytest  # noqa: F401
        assert pytest.__version__

    def test_flake8_importable(self):
        import flake8  # noqa: F401


# ── Architectural Regression Tests ────────────────────────────────────────────

class TestArchitectural:
    """
    These tests will FAIL if circular imports exist between services
    and PASS once the dependency structure is corrected.
    """

    def test_user_service_importable(self):
        from src.services.user import UserService  # noqa: F401

    def test_order_service_importable(self):
        from src.services.order import OrderService  # noqa: F401

    def test_both_services_importable_together(self):
        """This is the key test — fails with circular imports."""
        from src.services.user import UserService
        from src.services.order import OrderService
        u = UserService()
        o = OrderService()
        assert u.get_name(1) == "Alice"
        assert o.get_orders_for(1) == ["order_101", "order_102"]

    def test_user_service_order_count(self):
        """Requires cross-domain data (order count) via src.data, not OrderService."""
        from src.services.user import UserService
        assert UserService().get_order_count(1) == 2
        assert UserService().get_order_count(2) == 0

    def test_order_service_summary_includes_name(self):
        """Requires cross-domain data (user name) via src.data, not UserService."""
        from src.services.order import OrderService
        summary = OrderService().get_order_summary(1)
        assert "Alice" in summary
        assert "2" in summary

    def test_no_cross_service_dependency(self):
        """Verify services only depend on src.data, not each other."""
        import importlib
        import sys

        # Clear cached modules to get a fresh import
        for mod in list(sys.modules.keys()):
            if "src.services" in mod:
                del sys.modules[mod]

        user_mod = importlib.import_module("src.services.user")
        order_mod = importlib.import_module("src.services.order")

        user_src = open(user_mod.__file__).read()
        order_src = open(order_mod.__file__).read()

        assert "from src.services.order" not in user_src, \
            "UserService must not import OrderService (circular dependency)"
        assert "from src.services.user" not in order_src, \
            "OrderService must not import UserService (circular dependency)"


# ── Utility Tests ─────────────────────────────────────────────────────────────

class TestUtils:
    def test_get_env_default(self):
        from src.utils import get_env
        result = get_env("NONEXISTENT_KEY_XYZ", "fallback")
        assert result == "fallback"

    def test_clamp_within_range(self):
        from src.utils import clamp
        assert clamp(5, 0, 10) == 5

    def test_clamp_below_min(self):
        from src.utils import clamp
        assert clamp(-5, 0, 10) == 0

    def test_clamp_above_max(self):
        from src.utils import clamp
        assert clamp(15, 0, 10) == 10
