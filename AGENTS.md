# Agent Instructions

## Setup

```bash
# Create virtual environment with Python 3.14
uv venv --python 3.14
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
uv pip install -e .
```

## Running Tests

```bash
# Run all unit tests
python -m pytest tests/

# Run specific test file
python -m pytest tests/test_parser.py

# Run with verbose output
python -m pytest -v
```
