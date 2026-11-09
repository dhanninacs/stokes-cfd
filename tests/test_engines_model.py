"""The engine catalogue and the `solver_settings` node type (T-214a).

**The catalogue is the one place the engine/turbulence pairing is stated**, so these tests are about
what the model refuses as much as what it accepts.
"""

from __future__ import annotations

import pytest

from stokes.api import Session
from stokes.engines.catalogue import ENGINES, built_engines, engine
from stokes.engines.model import SETTINGS_PATH, solver_specs
from stokes.errors import StokesError
from stokes.run.errors import SolveInvalid


def test_the_catalogue_lists_the_built_engine_and_the_unbuilt_one() -> None:
    """**Unbuilt engines stay listed with a reason**, so a UI can show what is coming rather than a
    shorter menu with no explanation."""
    ids = [spec.id for spec in ENGINES]
    assert "of14.incompressibleFluid" in ids
    built = [spec.id for spec in built_engines()]
    assert built == ["of14.incompressibleFluid", "of14.fluid"]
    assert all(spec.why for spec in ENGINES if not spec.built), "an unbuilt engine must say why"


def test_an_unknown_engine_is_solve_invalid_naming_the_known_ones() -> None:
    with pytest.raises(SolveInvalid) as caught:
        engine("of14.doesNotExist")
    assert caught.value.code == "solve.invalid"
    assert "of14.incompressibleFluid" in str(caught.value)


def test_the_settings_type_registers_under_solver() -> None:
    specs = {spec.type: spec for spec in solver_specs()}
    assert "solver_settings" in specs
    settings = specs["solver_settings"]
    names = {param.name for param in settings.params}
    assert names == {
        "engine",
        "host",
        "turbulence",
        "energy",
        "density_model",
        "molar_mass",
        "transient",
        "delta_t",
        "end_time",
        "adjustable_time_step",
        "max_courant",
        "write_time",
        "time_average",
        "average_start",
        "initial_velocity",
        "initial_intensity",
        "initial_length_scale",
        "initial_viscosity_ratio",
        "gravity",
        "operating_pressure",
        "iterations",
        "write_interval",
        "residual_target",
        "cores",
        "slice_origin",
        "slice_normal",
    }


def test_the_defaults_are_the_brief_s_values() -> None:
    settings = {spec.type: spec for spec in solver_specs()}["solver_settings"]
    defaults = {param.name: param.default for param in settings.params}
    assert defaults["engine"] == "of14.incompressibleFluid"
    assert defaults["turbulence"] == "laminar"
    assert defaults["energy"] is False, (
        "energy is opt-in: every existing project writes the same case"
    )
    assert defaults["gravity"] == [0.0, 0.0, 0.0]
    assert defaults["operating_pressure"] == 101325.0
    assert defaults["iterations"] == 500
    assert defaults["write_interval"] == 50
    assert defaults["residual_target"] == 1e-3
    assert defaults["cores"] == 1
    assert defaults["slice_normal"] == [0.0, 0.0, 1.0]
    assert defaults["slice_origin"] == []


def test_a_length_parameter_is_a_quantity_and_a_plain_one_is_not() -> None:
    """**`slice_origin` is a length; `slice_normal` is not.** The one parameter that could be
    either is
    the one worth pinning: a bare float for a physical value is the bug ADR-0024 exists to
    prevent."""
    params = {p.name: p for p in {s.type: s for s in solver_specs()}["solver_settings"].params}
    assert params["slice_origin"].kind == "array"
    assert params["slice_origin"].dimension == "length"
    assert params["slice_normal"].kind == "array"
    assert params["slice_normal"].dimension is None


@pytest.fixture
def fresh(tmp_path, monkeypatch) -> Session:
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    session = Session()
    session.execute("project.new", name="t")
    return session


def test_solve_engines_lists_them_all(fresh: Session) -> None:
    reply = fresh.execute("solve.engines")
    listed = {item["id"]: item for item in reply["engines"]}
    assert listed["of14.incompressibleFluid"]["built"] is True
    # kOmegaSST is a turbulence model of the incompressible engine, no longer an engine of its own.
    assert "of14.kOmegaSST" not in listed
    assert "kOmegaSST" in listed["of14.incompressibleFluid"]["turbulence"]


def test_configure_creates_the_node_once(fresh: Session) -> None:
    first = fresh.execute("solve.configure", values={"iterations": 100})
    # **`created` is False now, and that is the G2 change.** `/solver/settings` exists from
    # `project.new`, so `solve.configure` edits the node it finds rather than adding one. What this
    # test is for — that a second configure does not add a SECOND node — is asserted below.
    assert first["created"] is False, "G2: the node is already there from project.new"
    assert first["path"] == SETTINGS_PATH
    second = fresh.execute("solve.configure", values={"iterations": 200})
    assert second["created"] is False, "the second configure edits rather than adding"
    node = fresh.project.find(SETTINGS_PATH)
    assert node.params["iterations"] == 200


def test_configure_can_set_the_engine_by_its_own_argument(fresh: Session) -> None:
    fresh.execute("solve.configure", engine="of14.incompressibleFluid")
    assert fresh.project.find(SETTINGS_PATH).params["engine"] == "of14.incompressibleFluid"


def test_an_unknown_engine_is_refused_and_the_tree_is_unchanged(fresh: Session) -> None:
    with pytest.raises(SolveInvalid):
        fresh.execute("solve.configure", engine="of14.nope")
    # **The node exists from `project.new` (G2), so "no node" is no longer the right assertion.**
    # What a refusal must not do is CHANGE it: the refusal belongs to the values, and the defaults
    # must be untouched.
    found = fresh.project.find(SETTINGS_PATH)
    assert found is not None, "G2: the settings node exists from project.new"
    assert found.params["engine"] == "of14.incompressibleFluid", "a refusal changed the defaults"


@pytest.mark.parametrize(
    ("values", "fragment"),
    (
        ({"iterations": 0}, "iterations"),
        ({"write_interval": 0}, "write_interval"),
        ({"residual_target": 0.0}, "residual_target"),
        ({"cores": 0}, "cores"),
    ),
)
def test_out_of_range_values_are_refused(fresh: Session, values: dict, fragment: str) -> None:
    """**The schema states kinds; a RANGE is a rule about the model**, so `solve.configure`
    checks it
    where a message can say why."""
    with pytest.raises(StokesError) as caught:
        fresh.execute("solve.configure", values=values)
    assert fragment in str(caught.value)


def test_a_refusal_leaves_the_tree_untouched(fresh: Session) -> None:
    """The rule `mesh.configure` follows: every command is built before any is pushed."""
    fresh.execute("solve.configure", values={"iterations": 100})
    before = fresh.project.find(SETTINGS_PATH).params["iterations"]
    with pytest.raises(StokesError):
        fresh.execute("solve.configure", values={"iterations": 5, "cores": -1})
    assert fresh.project.find(SETTINGS_PATH).params["iterations"] == before, (
        "the good value in the same call must not be applied either"
    )


def test_komega_sst_configure_is_allowed_and_solve_check_reports_it(fresh: Session) -> None:
    """**`solve.configure` accepts it; `solve.check` refuses it** — and the brief says where.

    The model's enum is the set of models a user may *choose*; "the writer for this one is not
    built" is
    a fact about the build, which a check reports. A configure-time refusal would also make a
    project
    saved with `kOmegaSST` impossible to open.
    """
    fresh.execute("solve.configure", values={"turbulence": "kOmegaSST"})
    assert fresh.project.find(SETTINGS_PATH).params["turbulence"] == "kOmegaSST"

    # The writer exists now (ADR-0028), so the check no longer reports it as unbuilt.
    reply = fresh.execute("solve.check")
    codes = [finding["code"] for finding in reply["findings"]]
    assert "solve.needs_turbulence" not in codes


def test_komega_sst_with_the_energy_engine_is_written_so_not_refused(fresh: Session) -> None:
    """**kOmegaSST is refused only where no writer exists**: the energy engine writes it."""
    fresh.execute(
        "solve.configure",
        values={"turbulence": "kOmegaSST", "energy": True, "engine": "of14.fluid"},
    )
    reply = fresh.execute("solve.check")
    assert "solve.needs_turbulence" not in [finding["code"] for finding in reply["findings"]]


def test_solve_check_reports_rather_than_raises(fresh: Session) -> None:
    """**A check is what a user runs to find out what is missing**, so it returns findings."""
    reply = fresh.execute("solve.check")
    assert reply["state"] == "not_ready"
    codes = [finding["code"] for finding in reply["findings"]]
    # **`solve.invalid` ("no settings") can no longer be reported here**, because G2 gives every
    # project a `/solver/settings` node from `project.new`. The finding still exists for a project
    # whose node was deleted, and that path is covered by `run.start`'s own test. What is left for a
    # brand-new project with no mesh is exactly one thing:
    assert "solve.no_mesh" in codes
    assert "solve.invalid" not in codes, "G2: the settings node now exists, so this cannot fire"
    assert reply["next"], "a finding must say what to do"


def test_solve_check_reports_a_missing_material_as_not_ready(fresh: Session) -> None:
    """**The snapshot refuses a set-up with no material, so the check must say so too.**

    Before this, `solve.check` reported "ready" for a project whose domain had no material, and the
    refusal only came at solve time. A project with no region has no domain part, which is the
    material's
    `solve.no_material` case.
    """
    reply = fresh.execute("solve.check")
    finding = next(f for f in reply["findings"] if f["code"] == "solve.no_material")
    assert finding["severity"] == "error"
    assert finding["suggestion"]
    assert reply["state"] == "not_ready"


def test_material_problem_is_none_only_when_the_snapshot_would_accept(fresh: Session) -> None:
    """`material_problem` is the snapshot's own rule, so it must give the same answer the
    snapshot would. With no domain part it returns the refusal, with its code."""
    from stokes.run.snapshot import material_problem

    problem = material_problem(fresh)
    assert isinstance(problem, SolveInvalid)
    assert problem.code == "solve.no_material"


def test_the_energy_engine_is_built_and_says_how_far_it_is_verified() -> None:
    """**Built since ADR-0027 step 5**, and `why` keeps the honest limit: the elbow ran 300
    iterations without its residuals reaching 1e-4."""
    from stokes.engines.catalogue import ENERGY_ENGINE

    spec = engine(ENERGY_ENGINE)
    assert spec.built is True and spec.available is True
    assert spec.kinds  # it writes the same boundary kinds as the incompressible engine


def test_energy_on_with_the_default_engine_is_refused_and_the_tree_is_unchanged(
    fresh: Session,
) -> None:
    """**Energy needs the fluid engine, so a refusal names the engine and changes nothing.**"""
    with pytest.raises(SolveInvalid) as caught:
        fresh.execute("solve.configure", values={"energy": True})
    assert "of14.fluid" in str(caught.value)
    assert fresh.project.find(SETTINGS_PATH).params["energy"] is False


def test_energy_on_with_the_fluid_engine_is_stored_and_check_does_not_refuse_it(
    fresh: Session,
) -> None:
    """**Stored, so the set-up survives a save; no longer refused by the check**, because the
    fluid engine is built (ADR-0027 step 5)."""
    from stokes.engines.catalogue import ENERGY_ENGINE

    fresh.execute("solve.configure", values={"engine": ENERGY_ENGINE, "energy": True})
    params = fresh.project.find(SETTINGS_PATH).params
    assert params["energy"] is True
    assert params["engine"] == ENERGY_ENGINE

    reply = fresh.execute("solve.check")
    assert "solve.energy_not_built" not in [f["code"] for f in reply["findings"]]


def test_energy_on_with_an_unbuilt_engine_is_refused_by_the_check(
    fresh: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The refusal stays for an unbuilt engine**: the finding names it rather than letting a run
    fail at the solver."""
    import dataclasses

    from stokes.engines.catalogue import ENERGY_ENGINE

    fresh.execute("solve.configure", values={"engine": ENERGY_ENGINE, "energy": True})
    unbuilt = tuple(
        dataclasses.replace(spec, built=False) if spec.id == ENERGY_ENGINE else spec
        for spec in ENGINES
    )
    monkeypatch.setattr("stokes.api.commands.solve.ENGINES", unbuilt)
    reply = fresh.execute("solve.check")
    finding = next(f for f in reply["findings"] if f["code"] == "solve.energy_not_built")
    assert finding["severity"] == "error"
    assert finding["suggestion"]
    assert reply["state"] == "not_ready"


def test_energy_on_asks_for_a_specific_heat_and_a_conductivity(
    fresh: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Asked only with energy on** (ADR-0027 decision 3): a flow-only case never reads them."""
    import stokes.run.snapshot as snapshot
    from stokes.engines.catalogue import ENERGY_ENGINE

    def missing(session: Session) -> SolveInvalid:
        return SolveInvalid("no specific heat", code="solve.no_thermal_material")

    monkeypatch.setattr(snapshot, "thermal_problem", missing)
    codes = lambda: [f["code"] for f in fresh.execute("solve.check")["findings"]]  # noqa: E731
    assert "solve.no_thermal_material" not in codes()
    fresh.execute("solve.configure", values={"engine": ENERGY_ENGINE, "energy": True})
    assert "solve.no_thermal_material" in codes()


def test_no_region_is_left_to_the_material_check(fresh: Session) -> None:
    """**A missing region is `material_problem`'s finding**, so the thermal check stays silent
    rather than reporting the same gap twice."""
    from stokes.run.snapshot import thermal_problem

    assert thermal_problem(fresh) is None


def test_gravity_and_pressure_are_refused_without_energy(fresh: Session) -> None:
    """**The flow alone does not read gravity or the operating pressure**, so a value for them
    with energy off would be a setting that silently does nothing (ADR-0027 step a)."""
    with pytest.raises(SolveInvalid) as caught:
        fresh.execute("solve.configure", values={"gravity": [0.0, 0.0, -9.81]})
    assert "energy equation" in str(caught.value)
    with pytest.raises(SolveInvalid):
        fresh.execute("solve.configure", values={"operating_pressure": 2e5})
    node = fresh.project.find(SETTINGS_PATH)
    # The refusal leaves the tree as it was: the values are still the defaults, if a node exists.
    assert node is None or node.params.get("gravity", [0.0, 0.0, 0.0]) == [0.0, 0.0, 0.0]
    assert node is None or node.params["operating_pressure"].value == 101325.0


def test_gravity_is_accepted_with_energy_in_the_same_change(fresh: Session) -> None:
    from stokes.engines.catalogue import ENERGY_ENGINE

    fresh.execute(
        "solve.configure",
        values={"engine": ENERGY_ENGINE, "energy": True, "gravity": [0.0, 0.0, -9.81]},
    )
    assert fresh.project.find(SETTINGS_PATH).params["gravity"] == [0.0, 0.0, -9.81]


def test_switching_the_engine_away_from_fluid_with_energy_on_is_refused(fresh: Session) -> None:
    """**Switching engine takes energy with it**: a switch to the default engine switches energy
    off (and the density model back to constant), so the settings never hold a pairing the writer
    refuses. Energy on with the default engine, asked for together, is still refused."""
    from stokes.engines.catalogue import ENERGY_ENGINE

    fresh.execute("solve.configure", values={"engine": ENERGY_ENGINE, "energy": True})
    fresh.execute("solve.configure", engine="of14.incompressibleFluid")
    params = fresh.project.find(SETTINGS_PATH).params
    assert params["engine"] == "of14.incompressibleFluid"
    assert params["energy"] is False and params["density_model"] == "constant"
    with pytest.raises(SolveInvalid):
        fresh.execute(
            "solve.configure", values={"engine": "of14.incompressibleFluid", "energy": True}
        )


def test_energy_off_clears_the_energy_finding(fresh: Session) -> None:
    from stokes.engines.catalogue import ENERGY_ENGINE

    fresh.execute("solve.configure", values={"engine": ENERGY_ENGINE, "energy": True})
    fresh.execute("solve.configure", values={"energy": False})
    reply = fresh.execute("solve.check")
    assert "solve.energy_not_built" not in [f["code"] for f in reply["findings"]]


@pytest.mark.parametrize("cores", [1, 2, 4])
def test_solver_core_count_is_saved_and_undoable(fresh: Session, cores: int) -> None:
    fresh.execute("solve.configure", values={"cores": cores})
    assert fresh.project.find(SETTINGS_PATH).params["cores"] == cores
    fresh.execute("history.undo")
    assert fresh.project.find(SETTINGS_PATH).params["cores"] == 1


@pytest.mark.parametrize("cores", [0, -2, 257, True, 1.5, "2"])
def test_invalid_solver_core_count_is_atomic(fresh: Session, cores: object) -> None:
    before = fresh.project.find(SETTINGS_PATH).params["iterations"]
    with pytest.raises(StokesError):
        fresh.execute("solve.configure", values={"cores": cores, "iterations": 42})
    assert fresh.project.find(SETTINGS_PATH).params["iterations"] == before
