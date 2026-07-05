param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $Args
)

python -m arrow_puzzle.cli @Args
