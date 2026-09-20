## Iterative Validation Policy

During interactive development, validation must be proportional to the
change.

For presentation-only changes confined to native Linux GUI code:

- build the affected target;
- run directly relevant fast tests;
- launch and visually verify the change;
- do not run full numerical/performance/cross-platform regression suites.

Run the complete regression suite only when:
- solver/controller/backend code changes;
- GPU resource ownership or synchronization changes;
- numerical behavior may have changed;
- the user explicitly requests full validation;
- preparing a merge/release/final acceptance.

Multiple small GUI changes should be accumulated and covered by one full
regression run at the end rather than repeating the full suite after every
iteration.
