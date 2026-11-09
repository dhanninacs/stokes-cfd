# Licence terms in plain words (README section draft)

**Draft for the owner.** Not yet linked from `README.md`. The binding text is `LICENSE`
(PolyForm Noncommercial License 1.0.0); this page only summarises it and does not add to it or
change it. Where this summary and `LICENSE` differ, `LICENSE` governs.

## Licence

Stokes is source-available under the
[PolyForm Noncommercial License 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0).
In plain words:

- **You may** read, run, copy and change the code, and fork the repository, for any
  noncommercial purpose: personal study, research, experiments, hobby projects and similar
  personal use with no anticipated commercial application.
- **Noncommercial organisations** (charities, schools and universities, public research, public
  safety or health and environmental organisations, and government institutions) may use it too.
- **You may share** copies and your changed versions, as long as whoever receives them also gets
  the licence terms (or their URL) and the `Required Notice:` line in `NOTICE`.
- **Commercial use is not licensed.** Using Stokes for a commercial purpose needs a separate
  written agreement from the copyright holder. Contact: `<CONTACT PLACEHOLDER>`.
- The software comes as is, without warranty. See `LICENSE` for the patent terms, the 32-day
  cure period for violations, and the full definitions.

Parts of this repository are under other licences:

- `tests/fixtures/of14/` is OpenFOAM content under the GNU GPL v3 (see `NOTICE`).
- Third-party packages installed by pip and npm keep their own licences (see `NOTICE` and
  `THIRD-PARTY-NOTICES.md`).

OPENFOAM® is a registered trade mark of OpenCFD Limited, producer and distributor of the
OpenFOAM software via www.openfoam.com. This product is not approved or endorsed by OpenCFD
Limited.

## Open points for the owner before publishing

- Decision A2 in `docs/00-open-questions.md` says "closed source"; publishing needs an ADR that
  supersedes it.
- `pyproject.toml` declares the classifier `License :: Other/Proprietary License`,
  `web/package.json` declares `"license": "ISC"`, and `THIRD-PARTY-NOTICES.md` §1 calls Stokes
  proprietary. All three should match the chosen licence.
- The contact address above is a placeholder.
