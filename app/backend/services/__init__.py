"""Service layer: data access + model calls, isolated from HTTP routing.

Services take their external clients by injection so unit tests can pass mocks.
When Lakebase or a warehouse is unavailable, services degrade to an in-memory
queue / demo values so the app still runs during a workshop.
"""
