import typing as T
import configparser
import os
import json
import logging
import sys
import threading

import requests
import click


GIT_DIR = '.git'
ZHUB_DIR = os.path.join(GIT_DIR, 'zhub')
ZENHUB_CONFIG_DIR = os.path.join(ZHUB_DIR, 'zenhub')
GITHUB_CONFIG_DIR = os.path.join(ZHUB_DIR, 'github')
ZENHUB_ENDPOINT = 'https://api.zenhub.io'
GITHUB_ENDPOINT = 'https://api.github.com'


LOG = logging.getLogger(__name__)


def findf(f: T.Callable[[T.Any], bool], lista: T.Iterable):
    for x in lista:
        assert x is not None
        if f(x):
            return x
    return None


def print_issue(issue, issue_by_number) -> None:
    full_issue = issue_by_number.get(issue['issue_number'])
    if full_issue:
        # click.echo('\t* ' + full_issue['number'] full_issue['title'])
        click.echo('\t#{number} {title}'.format(number=full_issue['number'], title=full_issue['title']))
        # click.echo()
        # click.echo('' + full_issue['body'])
        click.echo()


def print_pipeline(pipeline, issue_by_number) -> None:
    click.echo('# ' + pipeline['name'])
    click.echo()
    for issue in pipeline['issues']:
        print_issue(issue, issue_by_number)


def follow_links(links, **kwargs) -> T.Generator[requests.Response, None, None]:
    url = links.get('next', {}).get('url')
    while url:
        LOG.debug('following the next url %s', url)
        resp = requests.get(url, **kwargs)
        resp.raise_for_status()
        yield resp
        url = resp.links.get('next', {}).get('url')


def current_branch() -> str:
    path = os.path.join(GIT_DIR, 'HEAD')
    with open(path) as fp:
        content = fp.read()
    content = content.strip()
    _, ref = content.split(':')
    return ref.split('/')[-1]


def get_remote_url(branch: str):
    with open(os.path.join(GIT_DIR, 'config')) as fp:
        config = configparser.ConfigParser()
        config.read_file(fp)
    remote = config.get('branch "{}"'.format(branch), 'remote')
    return config.get('remote "{}"'.format(remote), 'url')


# FIXME
def parse_owner_repo_from_remote_url(url: str) -> T.Tuple[str, str]:
    host, owner_repo = url.split(':')
    return owner_repo[:-4].split('/')


def read_current_owner_repo() -> T.Tuple[str, str]:
    branch = current_branch()
    url = get_remote_url(branch)
    return parse_owner_repo_from_remote_url(url)


class ZenHubRemote:
    def __init__(self, endpoint: str, token=None):
        self.endpoint = endpoint
        self.token = token

    @property
    def _auth_header(self) -> dict:
        if self.token:
            return {'X-Authentication-Token': self.token}
        else:
            return {}

    def request_get(self, path: str, *args, **kwargs) -> requests.Response:
        kwargs.setdefault('headers', {}).update(self._auth_header)
        return requests.get(self.endpoint + path, *args, **kwargs)

    def request_post(self, path: str, *args, **kwargs) -> requests.Response:
        kwargs.setdefault('headers', {}).update(self._auth_header)
        return requests.post(self.endpoint + path, *args, **kwargs)

    def request_put(self, path: str, *args, **kwargs) -> requests.Response:
        kwargs.setdefault('headers', {}).update(self._auth_header)
        return requests.put(self.endpoint + path, *args, **kwargs)

    def fetch_board(self, repo_id: int) -> dict:
        resp = self.request_get('/p1/repositories/{repo_id}/board'.format(repo_id=repo_id))
        resp.raise_for_status()
        return resp.json()

    def move_issue(self, repo_id: int, issue_number: int, position: T.Union[str, int], pipeline_id: str) -> None:
        path = '/p1/repositories/{repo_id}/issues/{issue_number}/moves'.format(repo_id=repo_id, issue_number=issue_number)
        payload = {
            'pipeline_id': pipeline_id,
            'position': position,
        }
        resp = self.request_post(path, json=payload)
        resp.raise_for_status()

    def estimate(self, repo_id: int, issue_number: int, value: int) -> None:
        path = '/p1/repositories/{repo_id}/issues/{issue_number}/estimate'.format(repo_id=repo_id, issue_number=issue_number)
        payload = {
            'estimate': value
        }
        resp = self.request_put(path, json=payload)
        resp.raise_for_status()


class ZenHubLocal:
    def __init__(self, root: str):
        self.root = root

    def _read_board(self, repo_id: int) -> dict:
        with open(os.path.join(self.root, str(repo_id), 'board.json'), 'r') as fp:
            return json.load(fp)

    def events(self, repo_id: int) -> T.Generator[dict, None, None]:
        try:
            for line in open(os.path.join(self.root, str(repo_id), 'events')):
                yield json.loads(line)
        except FileNotFoundError:
            pass

    def remove_events(self, repo_id: int) -> None:
        try:
            os.remove(os.path.join(self.root, str(repo_id), 'events'))
        except FileNotFoundError:
            pass

    def fetch_board(self, repo_id: int) -> dict:
        board = self._read_board(repo_id)

        for event in self.events(repo_id):
            board = ZenHubLocal._apply_event(board, event)

        return board

    def write_board(self, repo_id: int, content) -> None:
        os.makedirs(os.path.join(self.root, str(repo_id)), exist_ok=True)
        with open(os.path.join(self.root, str(repo_id), 'board.json'), 'w') as fp:
            fp.write(json.dumps(content))

    def move_issue(self, repo_id: int, issue_number: int, position: T.Union[str, int], pipeline_id: str) -> None:
        board = self._read_board(repo_id)
        pidx, iidx = ZenHubLocal._find_issue_position(issue_number, board['pipelines'])
        if pidx < 0 or iidx < 0:
            raise ValueError('issue {} not found'.format(issue_number))

        with open(os.path.join(self.root, str(repo_id), 'events'), 'a') as fp:
            event = json.dumps({
                'type': 'move_issue',
                'body': {
                    'issue_number': issue_number,
                    'position': position,
                    'pipeline_id': pipeline_id,
                }
            })
            fp.write(event + '\n')

    def estimate(self, repo_id: int, issue_number: int, value: int) -> None:
        board = self._read_board(repo_id)
        pidx, iidx = ZenHubLocal._find_issue_position(issue_number, board['pipelines'])
        if pidx < 0 or iidx < 0:
            raise ValueError('issue {} not found'.format(issue_number))

        with open(os.path.join(self.root, str(repo_id), 'events'), 'a') as fp:
            event = json.dumps({
                'type': 'estimate',
                'body': {
                    'issue_number': issue_number,
                    'value': value,
                }
            })
            fp.write(event + '\n')

    @staticmethod
    def _find_issue_position(issue_number: int, pipelines: T.List) -> T.Tuple[int, int]:
        for pidx, pipeline in enumerate(pipelines):
            for iidx, issue in enumerate(pipeline['issues']):
                if issue['issue_number'] == issue_number:
                    return pidx, iidx
        return -1, -1

    @staticmethod
    def _find_pipeline_position(pipeline_id: str, pipelines: T.List) -> int:
        for idx, pipeline in enumerate(pipelines):
            if pipeline['id'] == pipeline_id:
                return idx
        return -1

    @staticmethod
    def _move_issue_locally(board, event_body) -> None:
        pipelines = board['pipelines']
        src_pidx, src_iidix = ZenHubLocal._find_issue_position(event_body['issue_number'], pipelines)
        dst_pidx = ZenHubLocal._find_pipeline_position(event_body['pipeline_id'], pipelines)
        if 0 <= src_pidx and 0 <= dst_pidx:
            assert 0 <= src_iidix

            found_issue = pipelines[src_pidx]['issues'].pop(src_iidix)
            assert found_issue

            for pos, issue in enumerate(pipelines[src_pidx]['issues']):
                issue['position'] = pos

            dst_issues = pipelines[dst_pidx]['issues']

            if event_body['position'] == 'top':
                dst_issues.insert(0, found_issue)
            elif event_body['position'] == 'bottom':
                dst_issues.append(found_issue)
            else:
                dst_issues.insert(event_body['position'], found_issue)

            for pos, issue in enumerate(dst_issues):
                issue['position'] = pos

    @staticmethod
    def _apply_event(board, event):
        if event['type'] == 'move_issue':
            ZenHubLocal._move_issue_locally(board, event['body'])
        elif event['type'] == 'estimate':
            pipelines = board['pipelines']
            pidx, iidx = ZenHubLocal._find_issue_position(event['body']['issue_number'], pipelines)
            if 0 <= pidx:
                pipelines[pidx]['issues'][iidx].setdefault('estimate', {})['value'] = event['body']['value']
        else:
            raise RuntimeError('invalid event: {}'.format(event['type']))
        return board


class GitHubRemote:
    def __init__(self, endpoint: str, token: str=None):
        self.endpoint = endpoint
        self.token = token

    @property
    def _auth_header(self) -> dict:
        if self.token:
            return {'Authorization': 'token {token}'.format(token=self.token)}
        else:
            return {}

    def request_get(self, path: str, *args, **kwargs) -> requests.Response:
        kwargs.setdefault('headers', {}).update(self._auth_header)
        return requests.get(self.endpoint + path, *args, **kwargs)

    def fetch_repo(self, owner: str, repo: str) -> dict:
        path = '/repos/{owner}/{repo}'.format(owner=owner, repo=repo)
        resp = self.request_get(path)
        resp.raise_for_status()
        return resp.json()

    def fetch_repo_issues(self, owner: str, repo: str, filters: dict = None) -> T.List[dict]:
        if filters is None:
            filters = {}
        path = '/repos/{owner}/{repo}/issues'.format(owner=owner, repo=repo)
        params = {'per_page': 100}
        params.update(filters)
        resp = self.request_get(path, params)
        resp.raise_for_status()
        issues = resp.json()
        for resp in follow_links(resp.links, headers=self._auth_header):
            issues += resp.json()
        return issues


def filter_issues(issues: list, filters: dict) -> T.Iterator[dict]:
    # filter by assignee
    assignee = filters.get('assignee')
    if assignee == 'none':
        f = lambda issue: len(issue['assignees']) <= 0
    elif assignee == '*':
        f = lambda issue: 0 < len(issue['assignees'])
    elif assignee:
        f = lambda issue: findf(lambda a: a['login'] == assignee, issue['assignees'])
    else:
        f = lambda issue: True
    issues = filter(f, issues)

    # filter by milestone
    milestone = filters.get('milestone')
    if milestone == 'none':
        f = lambda issue: issue.get('milestone') is None
    elif milestone == '*':
        f = lambda issue: issue.get('milestone') is not None
    elif milestone is not None:
        try:
            number = int(milestone)
        except ValueError:
            f = lambda issue: False
        else:
            f = lambda issue: issue.get('milestone') and issue['milestone'].get('number') == number
    else:
        f = lambda issue: True
    issues = filter(f, issues)

    # filter by labels
    labels = filters.get('labels')
    if labels is None:
        f = lambda issue: True
    else:
        if isinstance(labels, str):
            label_set = set(labels.split(','))
        else:
            label_set = set(labels)
        f = lambda issue: label_set.intersection(map(lambda label: label['name'], issue.get('labels', [])))
    issues = filter(f, issues)

    return issues


class GitHubLocal:
    def __init__(self, root: str):
        self.root = root

    def fetch_repo(self, owner: str, repo: str) -> dict:
        with open(os.path.join(self.root, owner, repo, 'repo.json')) as fp:
            return json.load(fp)

    def fetch_repo_issues(self, owner: str, repo: str, filters: dict = None) -> T.List[dict]:
        if filters is None:
            filters = {}
        with open(os.path.join(self.root, owner, repo, 'issues.json')) as fp:
            issues = json.load(fp)
        return list(filter_issues(issues, filters))

    def write_repo(self, owner: str, repo: str, content: dict) -> None:
        os.makedirs(os.path.join(self.root, owner, repo), exist_ok=True)
        with open(os.path.join(self.root, owner, repo, 'repo.json'), 'w') as f:
            f.write(json.dumps(content))

    def write_repo_issues(self, owner: str, repo: str, issues: T.List[dict]) -> None:
        os.makedirs(os.path.join(self.root, owner, repo), exist_ok=True)
        with open(os.path.join(self.root, owner, repo, 'issues.json'), 'w') as f:
            f.write(json.dumps(issues))


@click.group()
@click.option('--zenhub-token')
@click.option('--github-token')
@click.pass_context
def cli(ctx, zenhub_token, github_token):
    ctx.obj = {
        'github_token': github_token,
        'zenhub_token': zenhub_token,
    }

    LOG.debug('config %s', ctx.obj)


class LocalContext:
    def __init__(self, ctx_obj):
        self.ctx_obj = ctx_obj
        self.zenhub = ZenHubLocal(ZENHUB_CONFIG_DIR)
        self.github = GitHubLocal(GITHUB_CONFIG_DIR)
        self.cache = {}

    @property
    def repo(self):
        if 'repo' in self.cache:
            return self.cache['repo']

        owner, repo = read_current_owner_repo()

        try:
            repo = self.github.fetch_repo(owner, repo)
        except FileNotFoundError:
            raise click.ClickException('no issues found for {owner}/{repo} locally -- run \'zhub pull\' first'.format(
                owner=owner,
                repo=repo,
            ))

        self.cache['repo'] = repo
        return repo

    @property
    def board(self):
        if 'board' in self.cache:
            return self.cache['board']
        try:
            board = self.zenhub.fetch_board(self.repo['id'])
        except FileNotFoundError:
            raise click.ClickException('no board found locally -- run \'zhub pull\' first')
        self.cache['board'] = board
        return board

    def get_pipeline_by_id(self, pipeline_id: str) -> T.Union[None, T.Dict]:
        pipelines = self.board['pipelines']
        for pipeline in pipelines:
            if pipeline['id'] == pipeline_id:
                return pipeline
        return None

    def get_pipeline_by_name(self, pipeline_name: str) -> T.Union[None, T.Dict]:
        pipelines = self.board['pipelines']
        for pipeline in pipelines:
            if pipeline['name'].lower() == pipeline_name.lower():
                return pipeline
        return None

    def issues(self, assignee=None, milestone=None, labels=None):
        owner, repo = read_current_owner_repo()
        filters = {
            'assignee': assignee,
            'milestone': milestone,
            'labels': labels,
        }
        try:
            return self.github.fetch_repo_issues(owner, repo, filters=filters)
        except FileNotFoundError:
            raise click.ClickException('no issues found for {owner}/{repo} locally -- run \'zhub pull\' first'.format(
                owner=owner,
                repo=repo,
            ))


@cli.command(name='list', help='list issues')
@click.option('-a', '--assignee', help='filter issues by assignees')
@click.option('-m', '--milestone', help='filer issues by milestone')
@click.option('-l', '--labels', help='filer issues by comma separated labels')
@click.option('--json', 'fmt_json', is_flag=True, default=False, help='print issues as JSON')
@click.argument('pipelines', nargs=-1)
@click.pass_context
def cli_list(ctx, assignee, milestone, labels, fmt_json, pipelines):
    lctx = LocalContext(ctx.obj)

    issues = lctx.issues(assignee=assignee, milestone=milestone, labels=labels)
    issue_by_number = {}
    for issue in issues:
        issue_by_number[issue['number']] = issue

    if pipelines:
        found_pipelines = [
            fp for fp in lctx.board['pipelines']
            if findf(lambda p: p.lower() == fp['name'].lower(), pipelines)
        ]
    else:
        found_pipelines = lctx.board['pipelines']

    if fmt_json:
        print(json.dumps(found_pipelines))
    else:
        for pipeline in found_pipelines:
            print_pipeline(pipeline, issue_by_number)


def read_issues_from_pipelines(pipelines: T.Iterator):
    issues = []
    for pp in pipelines:
        for issue in pp['issues']:
            issues.append(issue['issue_number'])
    return issues


@cli.command(name='move', help='move issues to the pipeline')
@click.argument('pipeline', nargs=1)
@click.argument('issues', nargs=-1, type=int)
@click.pass_context
def cli_move(ctx, pipeline: str, issues: T.List[int]):
    lctx = LocalContext(ctx.obj)

    if len(issues) <= 0:
        try:
            issues = read_issues_from_pipelines(json.load(sys.stdin))
        except ValueError:
            raise click.BadArgumentUsage('issue number is expected')

    found_pipeline = findf(lambda p: p['name'].lower() == pipeline.lower(), lctx.board['pipelines'])

    if found_pipeline:
        zenhub = ZenHubLocal(ZENHUB_CONFIG_DIR)
        for issue_number in issues:
            zenhub.move_issue(
                lctx.repo['id'],
                issue_number=issue_number,
                position='bottom',
                pipeline_id=found_pipeline['id'],
            )
    else:
        raise click.ClickException('pipeline "{}" not found'.format(pipeline))


@cli.command(name='estimate', help='estimate issues')
@click.argument('value', type=int)
@click.argument('issues', type=int, nargs=-1)
@click.pass_context
def cli_estimate(ctx, value, issues):
    lctx = LocalContext(ctx.obj)

    if len(issues) <= 0:
        try:
            issues = read_issues_from_pipelines(json.load(sys.stdin))
        except ValueError:
            raise click.BadArgumentUsage('issue number is expected')

    zenhub = ZenHubLocal(ZENHUB_CONFIG_DIR)
    for issue_number in issues:
        zenhub.estimate(lctx.repo['id'], issue_number, value)


@cli.command(name='pull', help='pull issues')
@click.option('-a', '--assignee', help='filter issues by assignees')
@click.option('-m', '--milestone', help='filer issues by milestone')
@click.option('-l', '--labels', multiple=True, help='filer issues by comma separated labels')
@click.pass_context
def cli_pull(ctx, assignee, milestone, labels):
    github_local = GitHubLocal(GITHUB_CONFIG_DIR)
    github_remote = GitHubRemote(GITHUB_ENDPOINT, token=ctx.obj['github_token'])

    zenhub_local = ZenHubLocal(ZENHUB_CONFIG_DIR)
    zenhub_remote = ZenHubRemote(ZENHUB_ENDPOINT, token=ctx.obj['zenhub_token'])

    owner, repo = read_current_owner_repo()

    def _pull_repo_and_board():
        LOG.info('pulling repo %s/%s', owner, repo)
        repo_content = github_remote.fetch_repo(owner, repo)
        github_local.write_repo(owner, repo, repo_content)
        LOG.info('pulling ZenHub board for %s/%s', owner, repo)
        board = zenhub_remote.fetch_board(repo_content['id'])
        zenhub_local.write_board(repo_content['id'], board)

    t1 = threading.Thread(target=_pull_repo_and_board)

    def _pull_issues():
        filters = {
            'assignee': assignee,
            'milestone': milestone,
            'labels': labels,
        }
        LOG.info('pulling issues from %s/%s with filters: %s', owner, repo, json.dumps(filters))
        issues = github_remote.fetch_repo_issues(owner, repo, filters=filters)
        github_local.write_repo_issues(owner, repo, issues)
        LOG.info('fetched %s issues', len(issues))

    t2 = threading.Thread(target=_pull_issues)

    t1.start()
    t2.start()

    t1.join()
    t2.join()


def print_event(event, lctx: LocalContext):
    if event['type'] == 'move_issue':
        body = event['body']
        click.echo('move issue #{issue_number} to {pipeline} at position {position}'.format(
            issue_number=body['issue_number'],
            pipeline=lctx.get_pipeline_by_id(body['pipeline_id'])['name'],
            position=body['position'],
        ))
    elif event['type'] == 'estimate':
        body = event['body']
        click.echo('estimate issue #{issue_number} to {value}'.format(
            issue_number=body['issue_number'],
            value=body['value'],
        ))
    else:
        raise RuntimeError('invalid event {}'.format(event))


def print_events(events: T.Iterator, lctx: LocalContext):
    for event in events:
        print_event(event, lctx)


@cli.command(name='push', help='push pending changes')
@click.pass_context
def cli_push(ctx):
    zenhub_remote = ZenHubRemote(ZENHUB_ENDPOINT, ctx.obj['zenhub_token'])
    lctx = LocalContext(ctx.obj)

    for event in lctx.zenhub.events(lctx.repo['id']):
        print_event(event, lctx)
        if event['type'] == 'move_issue':
            body = event['body']
            zenhub_remote.move_issue(
                lctx.repo['id'],
                issue_number=body['issue_number'],
                position=body['position'],
                pipeline_id=body['pipeline_id'],
            )
        elif event['type'] == 'estimate':
            body = event['body']
            zenhub_remote.estimate(
                lctx.repo['id'],
                issue_number=body['issue_number'],
                value=body['value'],
            )
        else:
            raise RuntimeError('invalid event {}'.format(event))

    lctx.zenhub.remove_events(lctx.repo['id'])


@cli.command(name='events', help='list pending changes')
@click.pass_context
def cli_events(ctx):
    lctx = LocalContext(ctx.obj)
    print_events(lctx.zenhub.events(lctx.repo['id']), lctx)


@cli.command(name='revert', help='remove pending changes')
@click.pass_context
def cli_revert(ctx):
    lctx = LocalContext(ctx.obj)
    lctx.zenhub.remove_events(lctx.repo['id'])


def configure_log(logger: logging.Logger) -> None:
    formatter = logging.Formatter('%(asctime)s - %(levelname)-8s - %(name)s:%(lineno)s - %(message)s')

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.setLevel(logging.INFO)
    logger.addHandler(handler)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)
    handler.setLevel(logging.WARNING)
    logger.addHandler(handler)

    logger.setLevel(logging.INFO)


if __name__ == '__main__':
    configure_log(LOG)
    cli(auto_envvar_prefix='ZHUB')