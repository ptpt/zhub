# zhub
Command line tool for ZenHub

## Usage

```
Usage: zhub.py [OPTIONS] COMMAND [ARGS]...

Options:
  --zenhub-endpoint TEXT
  --zenhub-token TEXT
  --github-endpoint TEXT
  --github-token TEXT
  --repository TEXT       specify as {owner}/{repo}
  --help                  Show this message and exit.

Commands:
  estimate  estimate issues
  events    list events
  list      list issues
  move      move issues to the pipeline
  pull      pull repository issues from ZenHub and GitHub
  push      push changes to ZenHub and GitHub
```
