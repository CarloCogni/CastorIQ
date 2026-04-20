# facilities/models/__init__.py
"""FM domain models.

Internally partitioned by concern (assets, work, maintenance, systems, sensors,
docs, costs, exports). Each submodule is added in its own milestone.

M0 introduces no new models in this app — the role model lives in
``environments/models.py`` so it can be reused beyond FM.
"""
