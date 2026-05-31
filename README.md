# Thesis Pipeline — Self-Healing CI/CD

Bachelor Thesis | Valentin Jurke | Provadis Hochschule

---

## Repo Structure

```
thesis-pipeline/
├── src/
│   ├── calculator.py          ← Functional scenario target
│   ├── utils.py               ← Syntactic scenario target  
│   ├── data.py                ← Shared data layer (no circular deps)
│   └── services/
│       ├── user.py            ← Architectural scenario target
│       └── order.py           ← Architectural scenario target
├── tests/
│   └── test_all.py            ← Full test suite (green baseline)
├── scenarios/                 ← Broken files injected by the agent
│   ├── functional_regression.py
│   ├── syntactic_regression.py
│   ├── configurational_regression.txt
│   ├── architectural_regression_user.py
│   └── architectural_regression_order.py
├── .github/workflows/
│   └── ci.yml                 ← The pipeline (3 jobs)
├── requirements.txt           ← Configurational scenario target
├── pytest.ini
└── setup.cfg
```

## CI Jobs

| Job | Tool | Fails when... |
|-----|------|--------------|
| Lint | flake8 | PEP8 violations in `src/` |
| Unit Tests | pytest | Logic errors in source files |
| Architecture | python -c import | Circular imports between services |

## How Scenarios Work

The `scenarios/` folder contains pre-broken versions of source files.
The healing agent copies them over the healthy files to inject a failure,
then tries to fix them.

**Manual test (inject a failure yourself):**
```bash
# Inject functional regression
cp scenarios/functional_regression.py src/calculator.py
pytest   # → should fail

# Let the agent fix it (or fix manually to verify)
cp src/calculator.py.bak src/calculator.py   # restore
```

## Local Setup

```bash
pip install -r requirements.txt
pytest                  # run tests
flake8 src/ tests/      # run lint
```

## Green State

On `main`, all jobs should be green. Branches used for experiments
will intentionally have broken files injected by the agent.
