# Contributing to `powercontext`

Contributions are welcome, and they are greatly appreciated!
Every little bit helps, and credit will always be given.

You can contribute in many ways:

# Types of Contributions

## Report Bugs

Report bugs at https://github.com/oceanbase/powercontext/issues

If you are reporting a bug, please include:

- Your operating system name and version.
- Any details about your local setup that might be helpful in troubleshooting.
- Detailed steps to reproduce the bug.

## Fix Bugs

Look through the GitHub issues for bugs.
Anything tagged with "bug" and "help wanted" is open to whoever wants to implement a fix for it.

## Implement Features

Look through the GitHub issues for features.
Anything tagged with "enhancement" and "help wanted" is open to whoever wants to implement it.

## Write Documentation

powercontext could always use more documentation, whether as part of the official docs, in docstrings, or even on the web in blog posts, articles, and such.

## Submit Feedback

The best way to send feedback is to file an issue at https://github.com/oceanbase/powercontext/issues.

If you are proposing a new feature:

- Explain in detail how it would work.
- Keep the scope as narrow as possible, to make it easier to implement.
- Remember that this is a volunteer-driven project, and that contributions
  are welcome :)

# Get Started!

Ready to contribute? Here's how to set up `powercontext` for local development.
Please note this documentation assumes you already have `uv` and `Git` installed and ready to go.

1. Fork the `powercontext` repo on GitHub.

2. Clone your fork locally:

```bash
cd <directory_in_which_repo_should_be_created>
git clone git@github.com:YOUR_NAME/powercontext.git
```

3. Navigate into the repository:

```bash
cd powercontext
```

Then install the development environment and Git hooks:

```bash
make install
```

Recommended Codex skills are optional and are not required to build or test the project. If you have `npx`
available, install the skills pinned in `skills-lock.json` before starting a new Codex session:

```bash
make skills-install
```

4. Create a branch for local development:

```bash
git checkout -b name-of-your-bugfix-or-feature
```

Now you can make your changes locally.

5. Run the checks that match the change:

```bash
make check
make unit-test
```

Use `make e2e-test` for cross-component behavior, `make contract-test` for OpenAPI changes, and
`make docs-test` for documentation changes. `make test` runs the complete pytest suite.

Before raising a pull request you should also run tox when a change may affect supported Python versions.
This requires you to have the relevant Python versions installed. The same version matrix runs in CI.

6. Commit your changes and push your branch to GitHub:

```bash
git add .
git commit -m "Your detailed description of your changes."
git push origin name-of-your-bugfix-or-feature
```

7. Submit a pull request through the GitHub website.

# Pull Request Guidelines

Before you submit a pull request, check that it meets these guidelines:

1. Add behavior tests for new externally observable behavior. Add a regression test when a defect is likely to
   recur. Changes that provide neither do not need tests solely for coverage.

2. If the pull request adds functionality, the docs should be updated.
   Put your new functionality into a function with a docstring, and add the feature to the list in `README.md`.
