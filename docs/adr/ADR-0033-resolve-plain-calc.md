# ADR-0033: Resolve plain-arithmetic `#calc` in an imported case's copy

- Status: accepted
- Date: 2026-11-07
- Amends: ADR-0029 (importing OpenFOAM cases), ADR-0024 (the agent lint)

## Context

The `external` lint refuses `#calc`, because OpenFOAM compiles and runs C++ to evaluate it while
it reads a dictionary. That rule stays. But most tutorials use `#calc` only for arithmetic on
numbers defined in the same file - ballValve's mesh has
`rBend #calc "sqrt(scalar($rBall*$rBall - $rPipe*$rPipe))";` - and every such tutorial was
unloadable: opening ballValve from the start screen gave an empty project.

## Decision

When a case is imported, the project's own copy of each source file has every `#calc` whose
expression is arithmetic over plain-number entries of the same file replaced by its value
(`stokes/cases/calc.py`). The value is computed by Stokes' expression grammar
(`stokes.units.expressions`), never by `eval` or by OpenFOAM, and only these forms are accepted:
numbers; `$name`, `${name}`, `$<type>name`; `scalar(...)`/`label(...)` casts; `+ - * /`;
`pow(a, b)`; the grammar's functions (`sqrt`, `sin`, `cos`, `tan`, `exp`, `log`, `min`, `max`,
`abs`). Anything else - another dictionary's entry, a vector function, a name the file does not
define as a number - is left as written, and the lint refuses the file exactly as before. The
source case is never written; the manifest records each resolved expression under
`derived.calc`.

The lint itself is unchanged: a staged file still may not contain `#calc`.

## Consequences

- ballValve and the other tutorials whose `#calc` is arithmetic import.
- A resolved value is a number in the copy. Editing the original parameter (say `rBall`) in the
  copy no longer moves the computed one; the case's dictionaries say so with the value in place.
- The agent's own lint at job start sees only the resolved copy, so nothing on the host changes.
