# zhub
Command line tool for ZenHub

<!-- ## Installation

```shell
pip install zhub
``` -->

## Quickstart

Generate tokens:

1. GitHub token: https://github.com/settings/tokens
2. ZenHub token: https://app.zenhub.com/dashboard/tokens

Add your tokens to `~/.profile`
```shell
export ZHUB_GITHUB_TOKEN=YOUR_GITHUB_TOKEN
export ZHUB_ZENHUB_TOKEN=YOUR_ZENHUB_TOKEN
```

Pull all open issues assigned to `ptpt` in the repo `zhub`
```shell
# assume the repo is git-cloned: git clone git@github.com:ptpt/zhub.git
cd zhub
zhub pull -a ptpt
```

List all issues pulled down above
```shell
zhub list
```

Move issues `#1` `#2` `#3` to `Backlog`
```shell
zhub move backlog 1 2 3
```

Move all bugs from `Backlog` to `In Progress`
```shell
zhub list --json -l bug backlog | zhub move 'in progress'
```

Push the changes
```shell
zhub push
```

List all bugs
```shell
zhub list -l bug
```

## Usage

```
Usage: zhub.py [OPTIONS] COMMAND [ARGS]...

Options:
  --zenhub-token TEXT
  --github-token TEXT
  --help               Show this message and exit.

Commands:
  estimate  estimate issues
  events    list pending changes
  list      list issues
  move      move issues to the pipeline
  pull      pull issues
  push      push pending changes
  revert    remove pending changes
```
