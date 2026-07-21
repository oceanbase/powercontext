"""Console entry point for the PowerContext CLI."""

from powercontext.cli.app import create_cli


def main() -> None:
    """Run the CLI assembled from installed PowerContext components."""

    create_cli()()
