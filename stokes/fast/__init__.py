"""Fast mode (ADR-0047): a GPU lattice-Boltzmann preview of the case, on the implicit fields.

The fluid is the implicit field of the case's domain part (ADR-0035), compiled to a WGSL function;
the lattice runs on the desktop host's GPU through ``wgpu`` and follows edits to the model while it
runs. It is a preview: the engines (OpenFOAM, coupledSimpleFoam) are what a result rests on.

Modules, each importable on its own:

* ``errors``    the ``fast.*`` error codes
* ``wgsl``      field text (Fidget's format, ``stokes.implicit.graph.to_text``) to a WGSL function,
                the shaders' shared parts, the classification, the temperature's step, the samples
* ``kernel``    the lattice's step as WGSL: streaming in place, the wall and port links, the
                collisions and the turbulent preview's closures
* ``inplace``   where a population lives in the one buffer (the layout and its proof)
* ``lattice``   D3Q19 constants, the conversion between SI and lattice units, the closures
* ``grid``      the lattice's size and placement from the fluid's box and a fidelity
* ``ports``     planar inlets and outlets from tagged triangles, and their footprint masks
* ``case``      ``FastCase``: everything the solver needs, as plain data
* ``device``    the only module that imports ``wgpu``: buffers, pipelines, steps, samples
* ``runner``    the worker thread that owns the device and steps the lattice

Import rules (CLAUDE.md section 1, rule 5): headless. The standard library, numpy, ``stokes.errors``
and ``stokes.log`` at module level; ``wgpu`` inside ``stokes.fast.device`` only. Nothing here reads
the model: ``stokes.api.fast_inputs`` turns a session into a ``FastCase``.
"""
