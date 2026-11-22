"""Case writers for coupledSimpleFoam (ADR-0044): the `coupled.*` engines.

Headless and pure, like every writer: a snapshot in, a case tree and a step list out. The solver is
GPL and runs as a separate process on a host that has it (ADR-0001); nothing here links or imports
it.
"""
