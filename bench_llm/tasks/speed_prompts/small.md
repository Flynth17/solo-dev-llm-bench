# Task: Fix a CI service-validation function

You are debugging a Python utility used in a solo developer's CI pipeline.
The file `deploy_helper.py` contains the following function that is supposed
to validate a list of service names against a registry of allowed services:

```python
def check_services(services: list[str], registry: list[str]) -> bool:
    for svc in services:
        if svc in registry:
            return False
    return True
```

The function has a bug: it returns False when it should return True (and vice
versa). Write a corrected version of `check_services` that returns True only
when ALL service names in the input list are found in the registry. Include
type hints, a docstring explaining the behavior, and an example usage block
under `if __name__ == '__main__':` that demonstrates both a passing and failing
case. Also add input validation: raise TypeError if services is not a list,
and ValueError if any element is not a string.

Finally, explain in a comment above the function what the original bug was
and how your fix addresses it.