#! usr/bin/env python
# Helenka Casler, 2021
# Contact: HCasler on GitHub

import re
from datetime import datetime

from Mu2eCI.logger import log
from Mu2eCI import config
from Mu2eCI import test_suites
from Mu2eCI.common import (
    get_authorised_users,
    get_modified
)

class TestTriggerResult:
    NOCOMMAND = "nocommand"
    INVALIDINPUT = "invalidinput"
    GENERICFAIL = "genericfail"
    SUCCESS = "success"

class PRConditions:
    # Class that just hold the info we need to decide what to do with this PR.
    def __init__(self):
        self.prId = pr.id # int
        self.author = pr.user.login # str
        self.trustedAuthor = None # bool # DONE
        self.authorizedUsers = None # set # DONE
        self.authedTeams = None # set # DONE
        self.modifiedFolders = None # set DONE
        self.watcherList = None # set # DONE
        self.requiredTests = [] # list of str # DONE
        self.baseCommitSha = None # str # DONE
        self.lastCommit = None # git commit 
        self.baseCommitSha_lastTest = None # str # DONE
        self.commitStatusTime = {} # DONE
        self.baseHeadChanged = None # bool # DONE
        self.future_commit = None # bool
        self.future_commit_timedelta_string = None # str
        # commit test states:
        self.prev_test_statuses = {} # DONE
        self.test_urls = {}
        self.test_triggered = {} # DONE
        self.test_status_exists = {} # DONE
        self.tests_already_triggered = [] # DONE
        self.legit_tests = set() # DONE
        self.lastCommitDate = None
        self.newPR = None # bool # DONE
        self.commentsList = None # DONE
        self.lastTimeSeen = None # DONE
        self.previousBotComments = [] # str, comments BY the bot
        self.botInvokingComments = [] # gh comments, by others TAGGING the bot
        self.testTriggerResults = []
        self.testsRequested = [] # indices sync up with botInvokingComments
        self.extraEnvs = []


class PRConditionsBuilder:
    # Builder class for PRConditions. Use like:
    # prConditions = PRConditionsBuilder(gh, pr, repo)\
    #                .determineAuthorizations()\
    #                .determineModifiedFoldersAndWatchers()\
    #                .moreMethods()\
    #                .build()

    def __init__(self, gh, pr, repo):
        self.gh = gh
        self.pr = pr
        self.repo = repo
        self.prId = pr.id # int
        self.author = pr.user.login # str
        self.trustedAuthor = None # bool # DONE
        self.authorizedUsers = None # set # DONE
        self.authedTeams = None # set # DONE
        self.modifiedFolders = None # set DONE
        self.watcherList = None # set # DONE
        self.requiredTests = [] # list of str # DONE
        self.baseCommitSha = None # str # DONE
        self.lastCommit = None # git commit 
        self.baseCommitSha_lastTest = None # str # DONE
        self.commitStatusTime = {} # DONE
        self.baseHeadChanged = None # bool # DONE
        self.future_commit = None # bool
        self.future_commit_timedelta_string = None # str
        # commit test states:
        self.prev_test_statuses = {} # DONE
        self.test_urls = {}
        self.test_triggered = {} # DONE
        self.test_status_exists = {} # DONE
        self.tests_already_triggered = [] # DONE
        self.legit_tests = set() # DONE
        self.lastCommitDate = None
        self.newPR = None # bool # DONE
        self.commentsList = None # DONE
        self.lastTimeSeen = None # DONE
        self.previousBotComments = [] # str, comments BY the bot
        self.botInvokingComments = [] # gh comments, by others TAGGING the bot
        self.testTriggerResults = []
        self.testsRequested = [] # indices sync up with botInvokingComments
        self.extraEnvs = []
        
        

    def determineAuthorizations(self):
        # Is the author trusted to make PR's that get tested automatically?
        # Populate list of users who can invoke the tests manually.
        # Populate the list of teams that can invoke the tests manually.
        mu2eorg = self.gh.get_organization("Mu2e")
        self.trustedAuthor = mu2eorg.has_in_members(self.author)
        authorised_users, authed_teams = get_authorised_users(
            mu2eorg, self.repo, branch=self.pr.base.ref
        )
        self.authorizedUsers = authorised_users
        self.authedTeams = authed_teams
        if self.trustedAuthor:
            self.authorzedUsers.add(self.author)
        log.debug("Authorised Users: %s", ", ".join(self.authorizedUsers))
        return self

    def _isUserWatching(self, pkgpatt, modified_targs):
        # used internally by self.determineModifiedFoldersAndWatchers()
        watching = False
        regex_comp = re.compile(pkgpatt, re.I)
        for target in modified_targs:
            if (target == "/" and pkgpatt == "/") or regex_comp.match(
                target.strip()
            ):
                watching = True
                break
        return watching

    def determineModifiedFoldersWatchersTests(self):
        # Get the list of top-level folders that have been modified.
        # Get the list of watchers of those folders.
        # Get the default required tests for modifications to these folders.
        # Folders:
        self.modifiedFolders = get_modified(pr_files)
        # Watchers:
        watcherListInternal = []
        watchers = config.watchers
        log.debug("watchers: %s", ", ".join(watchers))
        modifiedTargs = [x.lower() for x in self.modifiedFolders]
        for user, packages in watchers.items():
            for pkgpatt in packages:
                userWatching = False
                try:
                    userWatching = self._isUserWatching(pkgpatt, modifiedTargs)
                except Exception:
                    log.warning(
                        "ERROR: Possibly bad regex for watching user %s: %s"
                        % (user, pkgpatt)
                    )
                if userWatching:
                    watcherListInternal.append(user)
        self.watcherList = set(watcherListInternal)
        # Required tests
        self.requiredTests = test_suites.get_tests_for(self.modifiedFolders)
        log.info("Tests required: %s", ", ".join(test_requirements))
        return self

    def _logCommitInfo(self, git_commit):
        last_commit_date = git_commit.committer.date
        log.info("Latest commit message: %s", git_commit.message.encode("ascii", "ignore"))
        log.info("Latest commit sha: %s", git_commit.sha)
        log.info("Merging into: %s %s", self.pr.base.ref, self.baseCommitSha)
        log.info("PR update time %s", self.pr.updated_at)
        log.info("Time UTC: %s", datetime.utcnow())
        self.future_commit = False
        self.future_commit_timedelta_string = None
        if last_commit_date > datetime.utcnow():
            future_td = last_commit_date - datetime.utcnow()
            if future_td.total_seconds() > 120:
                self.future_commit = True
                self.future_commit_timedelta_string = str(future_td) + " (hh:mm:ss)"
                log.warning("This commit is in the future! That is weird!")


    def _checkIfHeadChangedSinceLastTest(self, stat):
        log.debug("Check if this is when we last triggered the test.")
        name = "buildtest/last"
        if (
            name in self.commitStatusTime
            and commitStatusTime[name] > stat.updated_at
        ):
            return # this commit status is not the latest we've seen
        commitStatusTime[name] = stat.updated_at
        # this is the commit SHA in master that we used in the last build test
        self.baseCommitSha_lastTest = stat.description.replace(
            "Last test triggered against ", ""
        )

        log.info(
            "Last build test was run at base sha: %r, current HEAD is %r"
            % (self.baseCommitSha_lastTest, self.baseCommitSha)
        )

        if not self.baseCommitSha.strip().startswith(
            self.baseCommitSha_lastTest.strip()
        ):
            log.info(
                "HEAD of base branch is now different to last tested base branch commit"
            )
            self.baseHeadChanged = True
        else:
            log.info("HEAD of base branch is a match.")
            self.baseHeadChanged = False

    def determineCommitInfoAndTestStatus(self):
        # get the sha of the last commit on the base branch
        self.baseCommitSha = self.repo.get_branch(
            branch=self.pr.base.ref
        ).commit.sha
        # info on most recent commit in PR
        self.lastCommit = self.pr.get_commits().reversed[0]
        git_commit = self.lastCommit.commit
        if git_commit is None:
            return
        self.lastCommitDate = git_commit.committer.date
        log.debug(
            "Latest commit by %s at %r",
            git_commit.committer.name,
            self.lastCommitDate,
        )
        self._logCommitInfo(git_commit)
        
        # now get commit statuses
        # this is how we figure out the current state of tests
        # on the latest commit of the PR.
        commit_status = last_commit.get_statuses()
        # we can translate git commit status API 'state' strings if needed.
        state_labels = config.main["labels"]["states"]
        state_labels_colors = config.main["labels"]["colors"]
        for stat in commit_status:
            name = test_suites.get_test_name(stat.context)
            log.debug(f"Processing commit status: {stat.context}")
            if "buildtest/last" in stat.context:
                self._checkIfHeadChangedSinceLastTest(stat)
                continue
            if name == "unrecognised":
                continue

            if name in self.commitStatusTime and self.commitStatusTime[name] > stat.updated_at:
                continue

            self.commitStatusTime[name] = stat.updated_at

            # error, failure, pending, success
            self.prev_test_statuses[name] = stat.state
            if stat.state in state_labels:
                self.prev_test_statuses[name] = state_labels[stat.state]
            self.legit_tests.add(name)
            self.test_status_exists[name] = True
            if (
                name in self.test_triggered and self.test_triggered[name]
            ):  # if already True, don't change it
                continue

            self.test_triggered[name] = (
                ("has been triggered" in stat.description)
                or (stat.state in ["success", "failure"])
                or ("running" in stat.description)
            )

            # some other labels, gleaned from the description (the status API
            # doesn't support these states)
            if "running" in stat.description:
                self.prev_test_statuses[name] = "running"
                self.test_urls[name] = str(stat.target_url)
            if "stalled" in stat.description:
                self.prev_test_statuses[name] = "stalled"
        return self

    def determineIfNew(self):
        not_seen_yet = True
        last_time_seen = None
        if self.commentsList is None:
            issue = self.repo.get_issue(self.prId)
            self.commentsList = issue.issue.get_comments()
        for comment in self.commentsList:
            # loop through once to ascertain when the bot last commented
            if comment.user.login == config.main["bot"]["username"]:
                if last_time_seen is None or last_time_seen < comment.created_at:
                    not_seen_yet = False
                    last_time_seen = comment.created_at
                    log.debug(
                        "Bot user comment found: %s, %s",
                        comment.user.login,
                        str(last_time_seen),
                    )
        log.info("Last time seen %s", str(last_time_seen))
        self.newPR = not_seen_yet
        self.lastTimeSeen = last_time_seen
        return self

    def _shouldIgnoreComment(self, comment):
        # Ignore all messages which are before last commit.
        if comment.created_at < self.lastCommitDate:
            log.debug("IGNORE COMMENT (before last commit)")
            return True
        # neglect comments we've already responded to
        if self.lastTimeSeen is not None and (comment.created_at < self.lastTimeSeen):
            log.debug(
                "IGNORE COMMENT (seen) %s %s < %s",
                comment.user.login,
                str(comment.created_at),
                str(self.lastTimeSeen),
            )
            return True
        # neglect comments by un-authorised users
        if (
            comment.user.login not in self.authorizedUsers
            or comment.user.login == config.main["bot"]["username"]
        ):
            log.debug(
                "IGNORE COMMENT (unauthorised, or bot user) - %s", comment.user.login
            )
            return True


    def determineIfBotInvoked(self):
        bot_comments = (
            []
        )  # keep a track of our comments to avoid duplicate messages and spam.
        comments = self.pr.as_issue().get_comments()
        for comment in comments:
            if comment.user.login == config.main["bot"]["username"]:
                bot_comments += [comment.body.strip()]
            # comments we should ignore
            if self._shouldIgnoreComment(comment):
                continue
            for react in comment.get_reactions():
                if react.user.login == config.main["bot"]["username"]:
                    log.debug(
                        "IGNORE COMMENT (we've seen it and reacted to say we've seen it) - %s",
                        comment.user.login,
                    )
            testTriggerResult = None
            testRequested = None
            extraEnv = None
            trigger_search, mentioned = None, None

            # now look for bot triggers
            # check if the comment has triggered a test
            try:
                trigger_search, mentioned = check_test_cmd_mu2e(
                    comment.body, repo.full_name
                )
            except ValueError:
                log.exception("Failed to trigger a test due to invalid inputs")
                testTriggerResult = TestTriggerResult.INVALIDINPUT
            except Exception:
                log.exception("Failed to trigger a test.")
                testTriggerResult = TestTriggerResult.GENERICFAIL
            if trigger_search is not None:
                tests, _, extra_env = trigger_search
                log.info("Test trigger found!")
                log.debug("Comment: %r", comment.body)
                log.debug("Environment: %s", str(extra_env))
                #log.info("Current test(s): %r" % tests_to_trigger)
                log.info("Adding these test(s): %r" % tests)
                testTriggerResult = TestTriggerResult.SUCCESS
                testRequested = tests
                extraEnv = extra_env
            elif mentioned:
                # we didn't recognise any commands!
                testRequested = TestTriggerResult.NOCOMMAND
            if testTriggerResult is None:
                # just in case
                testTriggerResult = TestTriggerResult.GENERICFAIL
            self.botInvokingComments.append(comment)
            self.testsRequested.append(testRequested)
            self.testTriggerResults.append(testTriggerResult)
            self.extraEnvs.append(extraEnv)
        self.previousBotComments = bot_comments
        return self

    def build(self):
        prConditions = PRConditions()
        prConditions.prId = self.prId
        prConditions.author = self.author
        prConditions.trustedAuthor = self.trustedAuthor
        prConditions.authorizedUsers = self.authorizedUsers
        prConditions.authedTeams = self.authedTeams
        prConditions.modifiedFolders = self.modifiedFolders
        prConditions.watcherList = self.watcherList
        prConditions.requiredTests = self.requiredTests
        prConditions.baseCommitSha = self.baseCommitSha
        prConditions.lastCommit = self.lastCommit
        prConditions.baseCommitSha_lastTest = self.baseCommitSha_lastTest
        prConditions.commitStatusTime = self.commitStatusTime
        prConditions.baseHeadChanged = self.baseHeadChanged
        # commit test states:
        prConditions.prev_test_statuses = self.prev_test_statuses
        prConditions.test_urls = self.test_urls
        prConditions.test_triggered = self.test_triggered
        prConditions.test_status_exists = self.test_status_exists
        prConditions.tests_already_triggered = self.tests_already_triggered
        prConditions.legit_tests = self.legit_tests
        prConditions.lastCommitDate = self.lastCommitDate
        prConditions.newPR = self.newPR
        prConditions.commentsList = self.commentsList
        prConditions.lastTimeSeen = self.lastTimeSeen
        prConditions.previousBotComments = self.previousBotComments
        prConditions.botInvokingComments = self.botInvokingComments
        prConditions.testTriggerResults = self.testTriggerResults
        prConditions.testsRequested = self.testsRequested
        prConditions.extraEnvs = self.extraEnvs
        prConditions.future_commit = self.future_commit
        prConditions.future_commit_timedelta_string =  self.future_commit_timedelta_string

        return prConditions 

    @staticmethod
    def generate(gh, pr, repo):
        conditions = PRConditionsBuilder(gh, pr, repo).\
                     determineAuthorizations().\
                     determineModifiedFoldersWatchersTests().\
                     determineCommitInfoAndTestStatus().\
                     determineIfNew().\
                     determineIfBotInvoked().\
                     build()
        return conditions




