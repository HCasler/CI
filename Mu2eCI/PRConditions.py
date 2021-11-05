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

class PRConditionsBuilder:
    # Builder class for PRConditions. Use like:
    # prConditions = PRConditionsBuilder(gh, pr, repo)\
    #                .determineAuthorizations()\
    #                .determineModifiedFoldersAndWatchers()\
    #                .moreMethods()\
    #                .build()
    # As long as you call the constructor first and build() last, you should 
    # be able to call the other methods in any order.

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
        # commit test states:
        self.test_statuses = {} # DONE
        self.test_triggered = {} # DONE
        self.test_status_exists = {} # DONE
        self.tests_already_triggered = [] # DONE
        self.legit_tests = set() # DONE
        
        self.newPR = None # bool

        
        
        self.last_time_seen = None
        self.labels = set()
        

        # tests we'd like to trigger on this commit
        self.tests_to_trigger = []
        
        

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
        future_commit = False
        future_commit_timedelta_string = None
        if last_commit_date > datetime.utcnow():
            future_td = last_commit_date - datetime.utcnow()
            if future_td.total_seconds() > 120:
                future_commit = True
                future_commit_timedelta_string = str(future_td) + " (hh:mm:ss)"
                log.warning("This commit is in the future! That is weird!")


    def _checkIfHeadChangedSinceLastTest(self):
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
            last_commit_date,
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
                self._checkIfHeadChangedSinceLastTest()
                continue
            if name == "unrecognised":
                continue

            if name in self.commitStatusTime and self.commitStatusTime[name] > stat.updated_at:
                continue

            self.commitStatusTime[name] = stat.updated_at

            # error, failure, pending, success
            self.test_statuses[name] = stat.state
            if stat.state in state_labels:
                self.test_statuses[name] = state_labels[stat.state]
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
                self.test_statuses[name] = "running"
                self.test_urls[name] = str(stat.target_url)
            if "stalled" in stat.description:
                self.test_statuses[name] = "stalled"