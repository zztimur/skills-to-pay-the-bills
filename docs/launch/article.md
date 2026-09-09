# Your agent finished. Can someone else verify its work?

A useful answer should survive the conversation that produced it. For a financial support workflow, that means retaining the source, transformations, assumptions, and review decisions alongside the result.

FBAR Proof Kit provides a concrete example. Its synthetic demo starts with four fictional quarterly statements and produces a reviewed ledger, FX evidence, summary, and portable postflight manifest. The inputs are deliberately fictional so another developer can reproduce the workflow without handling anyone's financial records.

## The more revealing example removes Q2

With a quarter missing, the demo reports `insufficient-records` and `not-determinable`. Ledger confirmation fails. This is a specific tested missing-period case, not a claim that every possible bad input is detected.

[Watch the existing demonstration](../assets/demo/fbar-proof-demo-terminal.gif).

A failure result is useful when it identifies what prevents completion. Quietly carrying a balance across missing evidence can turn an unresolved assumption into an apparently precise annual answer. Keeping the gap visible lets the reviewer decide what evidence is needed.

## Keep the source attached to the number

The FX stage uses a frozen Treasury response in the reproducible example. It records the rate direction and retains the response with a workpaper. That makes it possible to inspect the provenance after the agent session ends. A frozen replay is reproducibility evidence; it is not a fresh lookup.

[Inspect the sample FX workpaper](../assets/demo/fbar-fx-proof-page-1.png).

## Keep review decisions visible

The synthetic fixture uses deterministic assertions to represent its review record. A real run must pause for human review. Script-level checks alone do not establish how an arbitrary model follows the skill instructions.

The postflight manifest binds retained artifacts and recomputes defined results. Integrity verification does not establish that all source evidence was sufficient or that a filing decision is legally correct.

## Try the example

From a cloned repository, with Python 3.11–3.13:

```bash
git submodule update --init --recursive
python3 -m pip install pdfplumber reportlab pillow
python3 examples/fbar-proof-demo/run_demo.py --check --skip-media
```

This runs the complete and missing-quarter paths in scratch space and checks the committed sample contract. The [demo guide](../../examples/fbar-proof-demo/README.md) explains the artifacts and review boundaries.

The reusable idea is straightforward: retain evidence, distinguish unresolved inputs, and make the handoff inspectable. Start with one real failure mode that your workflow can demonstrate honestly.
