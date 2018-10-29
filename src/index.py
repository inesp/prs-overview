import pytz
import requests
from datetime import datetime
from datetime import timedelta
from dateutil import parser

from flask import Flask
from flask import render_template
from flask import current_app

from secrets import REPOSITORY
from secrets import TOKEN
from secrets import ADMIN_USERS_NAME


app = Flask(__name__)


ALL_PRs = None  # PRs by ID
ALL_PEOPLE = None  # users by login name
NOW = None


class MyExc(Exception):
    pass

@app.route('/')
def index():
    global NOW, ALL_PRs, ALL_PEOPLE

    NOW = datetime.now(pytz.utc)
    ALL_PRs = {}  # PRs by ID
    ALL_PEOPLE = {}  # users by login name

    context = {'now': NOW}
    try:
        load_and_process_admin_person()

        raw_pr_data = load_open_prs()

        process_raw_pr_data(raw_pr_data)

        context['prs'] =  ALL_PRs

    except MyExc as exc:
        context['error'] = str(exc)

    return render_template('all_prs.html', **context)

def process_raw_pr_data(raw_pr_data):
    for raw_pr in raw_pr_data:
        pr_number = raw_pr['number']
        all_pr_data = load_and_process_each_pr(pr_number)
        PR(id=all_pr_data['id'], raw_data=all_pr_data)

    for pr in ALL_PRs.values():
        load_and_process_reviews_for(pr)


def load_and_process_admin_person():
    headers = {'Authorization': 'token {token}'.format(token=TOKEN)}
    url = ('https://api.github.com/users/{user}'
           .format(user=ADMIN_USERS_NAME))

    response = requests.get(url, headers=headers)

    if response.status_code != 200:
        raise MyExc(
            'I tried loading ADMIN user `{}`, but GitHub returned '
            'code `{}`.'.format(ADMIN_USERS_NAME, response.status_code)
        )

    raw_user_data = response.json()
    if not raw_user_data:
        raise MyExc(
            'Github returned no data about ADMIN user, none, zip, zero, zilch'
        )

    current_app.logger.info('ADMIN user loaded')

    PR_Person(raw_user_data)

def load_and_process_reviews_for(pr):
    headers = {'Authorization': 'token {token}'.format(token=TOKEN)}
    url = ('https://api.github.com/repos/{repo}/pulls/{pid}/reviews'
           .format(repo=REPOSITORY, pid=pr.raw_data['number']))

    response = requests.get(url, headers=headers)

    if response.status_code != 200:
        raise MyExc(
            'I tried loading reviews for PR `{}`, but GitHub returned '
            'code `{}`.'.format(pr.raw_data['number'], response.status_code)
        )

    raw_reviews = response.json()

    current_app.logger.info('REVIEWS loaded')

    for raw_review in raw_reviews:
        review = PR_Review(raw_review)
        ALL_PRs[pr.id_].append_review(review)


def load_open_prs():
    headers = {'Authorization': 'token {token}'.format(token=TOKEN)}
    url = ('https://api.github.com/repos/{repo}/pulls'
           '?state=OPEN'
           '&sort=created'
           '&order=desc'.format(repo=REPOSITORY))

    response = requests.get(url, headers=headers)

    if response.status_code != 200:
        raise MyExc(
            'I tried loading all PRs from repo `{}`, but GitHub returned '
            'code `{}`.'.format(REPOSITORY, response.status_code)
        )

    raw_pr_data = response.json()
    if not raw_pr_data:
        raise MyExc(
            'Github returned no data about PRs, none, zip, zero, zilch'
        )

    current_app.logger.info('PRs data loaded')

    return raw_pr_data

def load_and_process_each_pr(pr_number):
    # Unfortunately fetching list of PRs does not provide all data that we need,
    # thus I need to fetch each PR again....
    headers = {'Authorization': 'token {token}'.format(token=TOKEN)}
    url = ('https://api.github.com/repos/{repo}/pulls/{pid}'
           .format(repo=REPOSITORY, pid=pr_number))

    response = requests.get(url, headers=headers)

    if response.status_code != 200:
        raise MyExc(
            'I tried loading data about PR `{}` but GitHub returned '
            'code `{}`.'.format(pr_number, response.status_code)
        )

    raw_pr_data = response.json()
    if not raw_pr_data:
        raise MyExc(
            'Github returned no data about PR `{}`, none, zip, zero, zilch'
            .format(pr_number)
        )

    current_app.logger.info('One PR data loaded')

    return raw_pr_data


class PR(object):
    def __init__(self, id, raw_data):
        self.id_ = id
        self.raw_data = raw_data

        self.reviewers = []
        self.assessors = []
        self.role_less_people = []
        self.owner = None

        self.reviews = {}

        ALL_PRs[id] = self

        self._process_people()

        created = parser.parse(self.raw_data['created_at'])
        self.created_ago = NOW - created
        self.created_ago_level = self._ago_level(self.created_ago)

        updated = parser.parse(self.raw_data['updated_at'])
        self.updated_ago = NOW - updated
        self.updated_ago_level =self._ago_level(self.updated_ago)

    def _process_people(self):

        owner_data = self.raw_data['user']
        owner = get_or_create_person(owner_data['login'], owner_data)
        self.owner = owner

        for p_data in self.raw_data['assignees']:
            self.assessors.append(
                get_or_create_person(p_data['login'], p_data)
            )

        for p_data in self.raw_data['requested_reviewers']:
            self.reviewers.append(
                get_or_create_person(p_data['login'], p_data)
            )

    @property
    def created_ago_nice(self):
        nice_diff = str(self.created_ago).split('.')[0]
        return nice_diff

    @property
    def updated_ago_nice(self):
        nice_diff = str(self.updated_ago).split('.')[0]
        return nice_diff

    def _ago_level(self, diff):
        # levels: < 24H, < 48H, < 4 days, < 8 days, < 16 days, more
        if diff < timedelta(days=1):
            return 1
        if diff < timedelta(days=2):
            return 2
        if diff < timedelta(days=4):
            return 3
        if diff < timedelta(days=8):
            return 4
        if diff < timedelta(days=16):
            return 5
        return 6

    @property
    def waiting_for(self):

        labels = [label['name'].lower() for label in self.raw_data['labels']]

        if (
            'needs-work' in labels or 'add-tests' in labels
            or 'question' in labels
            or 'wrong-branch' in labels
        ):
            return [
                waiting_for(person=self.owner, reason='PR needs work', extra='Last commited/commented at TODO')
            ]

        if 'on-hold' in labels:
            reason = 'PR is too old'
            if self.created_ago_level >= 5:
                extra = 'This PR has not been marked as on-hold at TODO'
                return [
                    waiting_for(person=self.owner, reason=reason, extra=extra),
                    waiting_for(person=ALL_PEOPLE[ADMIN_USERS_NAME], reason=reason, extra=extra)
                ]
            return [
                waiting_for(person=self.owner, reason=reason,
                            extra='Last commited/commented at TODO')
            ]

        if 'question' in labels:
            # who added the question?
            return [
                waiting_for(person=self.owner, reason='TODO asked a question AT', extra='')
            ]

        # Any conflicts?
        if not self.raw_data['rebaseable']:
            return [
                 waiting_for(person=self.owner, reason='CONFLICTS!', extra='')
            ]


        # Have tests passed or not?
        # if not self.raw_data['mergeable']:
        #     return [
        #         waiting_for(person=self.owner, reason='Tests have not yet passed or are failing', extra='')
        #     ]

        # is approved by all?
        approve_needed_from = list(set(self.reviewers + self.assessors + self.role_less_people))
        must_review_people = []

        for person in approve_needed_from:

            review = self.reviews.get(person.login)
            review_state = review.raw_data['state'] if review else ''

            if review_state == '':
                must_review_people.append(waiting_for(person=person, reason='You have not yet reviewed.', extra=''))
            elif review_state != 'APPROVED':
                must_review_people.append(
                    waiting_for(person=person,
                                reason='Your review is still `{}`, '
                                       'you can review again.'.format(review_state),
                                extra=''))

        if must_review_people:
            return must_review_people

        # if everything good:
        if self.raw_data['mergeable_state'] == 'clean':
            merge_possible_by = list(set(self.assessors + self.reviewers))
            return [
                waiting_for(person=person, reason='ALL passed, just MERGE', extra='')
                for person in merge_possible_by
            ]


        # # TODO: anything else?
        if not self.raw_data['mergeable_state']:
            # probably tests have failed
            return [
                waiting_for(person=self.owner, reason='Probably TESTs have failed', extra='')
            ]

        return []


    def append_review(self, review):
        # take only the first found review for each person, because reviews are sorted by created DESC
        person = get_or_create_person(review.raw_data['user']['login'], review.raw_data['user'])
        if person.login in self.reviews:
            return

        if person.login == self.owner.login:
            return

        # if person is role-less, remeber her
        if person.login not in self.reviewers and person.login not in self.assessors:
            self.role_less_people.append(person)

        self.reviews[person.login] = review

    @property
    def approvals(self):
        result = []
        for review in self.reviews.values():
            if review.raw_data['state'] == 'APPROVED':
                person = get_or_create_person(review.raw_data['user']['login'],
                                              review.raw_data['user'])
                result.append(person)
        return result

def timedelta_to_nice(diff):
    s = diff.total_seconds()
    hours, remainder = divmod(s, 3600)
    minutes, seconds = divmod(remainder, 60)
    return '{:02}:{:02}:{:02}'.format(int(hours), int(minutes), int(seconds))



def get_or_create_person(login, raw_data):

    if login not in ALL_PEOPLE:
        PR_Person(raw_data)

    return ALL_PEOPLE[login]


class PR_Person(object):

    def __init__(self, raw_data):
        self.login = raw_data['login']
        self.raw_data = raw_data

        ALL_PEOPLE[self.login] = self


class PR_Review(object):

    def __init__(self, raw_data):
        self.raw_data = raw_data

        # dict_keys(['url', 'id', 'node_id', 'html_url', 'diff_url', 'patch_url',
        #            'issue_url', 'number', 'state', 'locked', 'title', 'user',
        #            'body', 'created_at', 'updated_at', 'closed_at', 'merged_at',
        #            'merge_commit_sha', 'assignee', 'assignees',
        #            'requested_reviewers', 'requested_teams', 'labels',
        #            'milestone', 'commits_url', 'review_comments_url',
        #            'review_comment_url', 'comments_url', 'statuses_url', 'head',
        #            'base', '_links', 'author_association'])

from collections import namedtuple
waiting_for = namedtuple('waiting_for', ['person', 'reason', 'extra'])
