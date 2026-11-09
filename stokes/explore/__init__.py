"""Design exploration numerics (ADR-0016): sampling, surrogates, sensitivity, Pareto utilities and
optimisation drivers, on numpy arrays and plain dataclasses.

The numerics know nothing about the study node model, the API, the ``function`` engine or the run
queue. They plug in through small protocols: a variable is a name plus a ``Domain``; a design is a
``DesignTable``; anything that turns design points into responses is an ``Evaluator``; a driver
reports progress through a ``GenerationCallback``; every failure is a ``StokesError`` with a
stable ``explore.*`` code (``errors``).

Modules, each importable on its own:

* ``domain``       variable domains (value list, range, uniform, truncated normal, categorical)
                   and their quantile maps to and from the unit interval
* ``space``        ``Variable`` and ``DesignSpace``: unit-cube scaling over whole point arrays
* ``table``        ``DesignTable``: variables, points, optional responses, sampler metadata
* ``sampling``     full factorial, Latin hypercube, Sobol, Halton, central composite,
                   Box-Behnken, optimal space filling, Monte Carlo; ``generate`` by name
* ``surrogate``    polynomial, radial basis and Gaussian-process surrogates, ``make_surrogate``,
                   ``fit_table``; ``validation`` has the metrics and k-fold cross-validation and
                   ``surrogate_base`` the protocol they share
* ``sensitivity``  Spearman and Pearson correlation, Sobol indices (SALib or built in, on a
                   function, a design or a surrogate), Morris screening
* ``problem``      ``Objective``, ``Constraint`` and ``Problem`` over named responses
* ``pareto``       non-dominated sorting, crowding distance, hypervolume, front extraction
* ``optimise``     the ``Evaluator`` protocol, differential evolution, Nelder-Mead and COBYLA,
                   NSGA-II and NSGA-III, the surrogate-assisted loop, ``OptimiseResult``
* ``functions``    analytic test problems with known answers (Branin, Rosenbrock, ZDT, DTLZ2, ...)

The study layer (Explore v1) sits beside them and imports no numpy except ``study_numerics``:

* ``model``            the study node types, registered with the built-in types
* ``definition``       a study node read into a ``StudyDefinition``
* ``authoring``        the undo steps, names and value checks the ``study.*`` commands use
* ``evaluate``         the study layer's ``Evaluator`` protocol, point requests and results
* ``engine_function``  the ``function`` engine: responses from expressions under overrides
* ``study_table``      design-table rows, metadata and the files under ``studies/<id>/``
* ``study_store``      the session's tables (``session.extensions["study"]``)
* ``study_numerics``   generation through ``sampling`` and Pareto fronts through ``pareto``

Import rules (CLAUDE.md section 1, rule 5, and ADR-0016): headless; numpy at module level is
fine, scipy, scikit-learn, SALib and pymoo are imported inside the functions that use them
(through ``_imports``) and only in this package. scipy is a core dependency; the other three
belong to the optional ``explore`` extra, and a missing one raises ``MissingDependency`` (code
``explore.missing_dependency``) naming the extra. Importing this package, or any module in it,
imports none of them. ``stokes.api`` imports only ``model`` (with the node types); the
``study.*`` commands import the rest inside their handlers.
"""
