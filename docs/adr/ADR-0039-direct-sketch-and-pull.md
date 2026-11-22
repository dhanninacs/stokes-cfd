# ADR-0039 · Sketch on the plane in the 3D view, and pull the body directly

**Status:** proposed · 2026-11-13 · amends ADR-0036 decision 4 (the sketcher as a 2D editor beside
the viewport); relies on ADR-0013 (the front end holds no model state), ADR-0035 (implicit
operations and their display mesh), ADR-0036's addendum (`parameter.update`, one Finish command)

## Context

The T0 live review of the modeller (2026-11-12, "Gap to Discovery" rows 1 to 4) found the flow
form-driven where the owner wants it direct, "seamless like Ansys Discovery": the sketcher was an
opaque 2D dialog over a hidden model, Finish opened an operation card with a Run button, the depth
was a typed field, and the viewport stayed empty for the 6 to 11 s the server took to mesh the
body. Profiling the extrude on this host (40 × 20 × 20 mm, warm worker, medians where repeated):
the 300 ms run-on-change debounce, then the mesher 2.07 s at the display cell (192 cells across,
57 k triangles; 0.57 s at 64 cells), then about 0.6 s of face grouping, packing, transport and
the client's build; a cold worker adds the OCP import. The ghost and the coarse pass below address
what the user sees during those seconds; the mesher's own speed is ADR-0035's.

## Decision

1. **The sketch is drawn over the 3D view, registered to it.** While the sketcher is open the
   camera is held orthographic and face-on to the sketch plane (`SceneManager.pinView`, from
   `direct/plane.ts`'s `planeCamera`): the plane's normal towards the viewer, its v axis up, the
   drawing's centre point under the drawing's centre and one millimetre the drawing's pixels per
   millimetre. An orthographic face-on view maps the plane to the screen by a translation and one
   scale, so the grid, the origin and every entity lie exactly over the model. The sketcher keeps
   pan and zoom; the camera follows them; nothing else moves it while pinned (fits, restores, the
   orbit controls and the free-area centring stand down). Cancel or Finish gives the previous view
   back. The drawing area is translucent (the model dimmed at about half, not blurred); where the
   drawing covers a side dock in a narrow window it is opaque over the dock only.
2. **A new sketch starts by choosing its plane** from three thumbnails (XY, YZ, ZX drawn in
   isometric) over the drawing; hovering one turns the camera to it, a click (or a tool key, or a
   click on the drawing) starts drawing. Planes through other origins and faces of bodies remain
   later work; an edited sketch is drawn at its own `origin`.
3. **Finish pulls at once.** A new drawn sketch becomes `operation.create implicit_sketch` with
   `method: extrude` and a default depth, and no card opens. The default depth is half the
   profile's larger side rounded to 1, 2, 2.5 or 5 × 10ⁿ mm (a 40 × 20 rectangle pulls 20 mm;
   10 mm without a profile). When the run is done its body is selected, which shows its handle.
   Revolve, symmetric and the other choices stay on the operation's page (Details).
4. **A Pull handle on a body made by an extrude.** With one such body picked, an arrow sits at the
   profile's centre on the pulled face along the pull direction (re-projected whenever the camera
   moves). Dragging it changes the depth along the arrow only (snapped to a step of at least four
   pixels, never through the plane; a symmetric pull moves its face half the depth) and writes it
   on release with one `parameter.update {depth}` (one journal record, one undo step, one rebuild).
   The value at the tip is a field: click, type a length (a bare number is millimetres) or an
   expression, Enter. A depth bound to an expression (`2 * W`) is shown with it and does not drag,
   so a drag never cuts a parametric link silently.
5. **A client-side ghost instead of an empty view.** While dragging, and from Finish or release
   until the rebuilt body is on screen, a translucent body in the accent colour is drawn from the
   2D profile by three.js (`ExtrudeGeometry` of the loops with their holes, even-odd as ADR-0036
   decision 2; no mesher, no API call). It is transient view state (`direct/ghostState.ts`): never
   sent, never stored, and it goes when the first mesh of the operation's output reaches the scene,
   when the run fails, or when no run starts. A drawn profile with bound coordinates, which the
   browser cannot evaluate, ghosts as the body's box.
6. **Implicit outputs are shown coarse first.** The worker meshes an implicit output at 64 cells
   across before its display cell when the display cell is at least 2.5 times finer and the
   display mesh is not cached; the coarse mesh goes out as a non-final pass, which the geometry
   store shows at once, and the final pass replaces it. The shape is sent with each pass and the
   last one counts. Measured: the first body on screen 1.4 to 2.2 s after the edit instead of 3.7
   to 4.4 s, for about 0.5 s more in all.
7. **Double-click and Edit re-enter the sketch** (the review's row 7, already in place through
   `app/editOperation.ts`); the sketch opens at its operation's origin, registered to the body.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Draw the sketch in WebGL in the scene (lines and snapping in view space) | The 2D editor's pointer, snapping and typed-size logic is tested and works in pixels; registering the camera to it gives the same picture with none of that rewritten. A 3D sketch on a picked face needs view-space picking anyway and comes with face sketching. |
| Keep the camera free and project the sketch into the perspective view | A perspective plane is not a scaled copy of the drawing, so every coordinate, grid line and snap would need the projection, and typed sizes would read differently across the plane. |
| Open the card after Finish with the depth pre-filled | One more step and a form between the profile and the body: the gap the review measured. The handle is the form. |
| Ghost by stretching the last body's mesh along the pull | Wrong at once for holes and for any change of the profile, and nothing to show for a new sketch. |
| A coarser display mesh for everything | A decision of ADR-0035 (the display cell is what faces are picked on); the coarse pass gives the speed without changing the final mesh. |

## Consequences

- The camera has a pinned state (`CameraRig.pin`/`unpin`); code that moves the camera must accept
  that it does nothing while a sketch is open.
- The direct flow is front-end only apart from the coarse pass: every change is an existing command
  (`operation.create`, `parameter.update`), and the journal and the API see what they always saw.
- An implicit run sends two meshes for a large body; a study engine meshing bodies directly
  (`stokes.explore.engine_geometry`) is unaffected.
- Tests: `web/src/direct/direct.test.ts` (plane mapping and the camera that registers the drawing,
  loops and holes, the ghost's geometry, default depth, drag to depth, typed depth);
  `tests/test_implicit_operations.py` (coarse before fine, cached runs skip it).
- Still open: sketching on a picked face or an offset plane, a pull on a face of any body (not only
  an extrude's end), a move gizmo, and a ghost for revolve, primitives and transforms.
