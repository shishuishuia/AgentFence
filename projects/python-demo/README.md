# Inventory Demo

A tiny inventory project used to demonstrate AgentFence Lite.

The code is intentionally small and contains a few realistic issues:

- discount handling in `checkout.py`
- a mutable default argument in `checkout.py`
- a boundary condition in `inventory.py`

Run the tests with:

```bash
python -m unittest discover -s tests
```
