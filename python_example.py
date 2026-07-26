"""Simple Python example executed by GitHub Actions."""


def summarize_numbers(numbers: list[float]) -> dict[str, float]:
    """Return the sum, average, and maximum of a non-empty number list."""
    if not numbers:
        raise ValueError("numbers must not be empty")

    total = sum(numbers)
    return {
        "sum": total,
        "average": total / len(numbers),
        "maximum": max(numbers),
    }


if __name__ == "__main__":
    values = [12, 18, 25, 7, 30]
    summary = summarize_numbers(values)

    print(f"Numbers: {values}")
    print(f"Sum: {summary['sum']}")
    print(f"Average: {summary['average']}")
    print(f"Maximum: {summary['maximum']}")
