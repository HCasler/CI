import re
from datetime import datetime
from socket import setdefaulttimeout

from Mu2eCI import config
from Mu2eCI import test_suites
from Mu2eCI.logger import log
from Mu2eCI.common import (
    api_rate_limits,
    post_on_pr,
    get_modified,
    get_authorised_users,
    check_test_cmd_mu2e,
    create_properties_file_for_test,
    get_build_queue_size,
)
from Mu2eCI.messages import (
    PR_SALUTATION,
    PR_AUTHOR_NONMEMBER,
    TESTS_ALREADY_TRIGGERED,
    TESTS_TRIGGERED_CONFIRMATION,
    JOB_STALL_MESSAGE,
    BASE_BRANCH_HEAD_CHANGED,
)
from Mu2eCI.PRConditions import PRConditionsBuilder

setdefaulttimeout(300)


def process_pr(gh, repo, issue, dryRun=False, child_call=0):
    if child_call > 2:
        log.warning("Stopping recursion")
        return
    api_rate_limits(gh)

    if not issue.pull_request:
        log.warning("Ignoring: Not a PR")
        return

    prId = issue.number
    pr = repo.get_pull(prId)

    if pr.changed_files == 0:
        log.warning("Ignoring: PR with no files changed")
        return

    # GitHub should send a pull_request webhook to Jenkins if the PR is merged.
    if pr.merged:
        # check that this PR was merged recently before proceeding
        # process_pr is triggered on all other open PRs
        # unless this PR was merged more than two minutes ago

        # Note: If the Jenkins queue is inundated, then it's likely this won't
        # work at the time. But, this is not likely to be more than just an intermittent problem.
        # This allows for a 2 minute lag.
        if (datetime.utcnow() - pr.merged_at).total_seconds() < 120:
            # Let people know on other PRs that (since this one was merged) the
            # base ref HEAD will have changed
            log.info(
                "Triggering check on all other open PRs as "
                "this PR was merged within the last 2 minutes."
            )
            pulls_to_check = repo.get_pulls(state="open", base=pr.base.ref)
            for pr_ in pulls_to_check:
                process_pr(
                    gh,
                    repo,
                    pr_.as_issue(),
                    dryRun,
                    child_call=child_call + 1,
                )

    if pr.state == "closed":
        log.info("Ignoring: PR in closed state")
        return

    # Collect all the info we need about this PR in order to decide what to do with it
    prConditions = PRConditionsBuilder.generate(gh, pr, repo)

    labels = set()
    watcher_text = ""
    # Explanantion for why we copy the data members of PRConditions here:
    # PRConditions hold the info retrieved from the PR, including stuff like
    # what the current status of the latest tests is, or which tests have
    # already been triggered. We will need to modify some of those values
    # as we make decisions about triggering new tests and updating the status.
    # Copying them to new variables first keeps the info retrieved from GitHub
    # clean, and separates the code that hold PR information from the code that
    # makes decisions and changes.
    tests_already_triggered = prConditions.tests_already_triggered
    doNotifyBaseHEADChanged = False
    stalled_jobs = []
    stalled_job_info = ""
    test_triggered = prConditions.test_triggered
    test_statuses = prConditions.prev_test_statuses
    test_status_exists = prConditions.test_status_exists
    tests_to_trigger = []

    # Notify the people watching the modified packages
    if len(prConditions.watcherList) > 0:
        watcher_text = (
            "The following users requested to be notified about "
            "changes to these packages:\n"
        )
        watcher_text += ", ".join(["@%s" % x for x in prConditions.watcherList])

    
    # If the base branch HEAD has changed since the last commit, and that 
    # commit got a build test (not a new PR), and the last commit shows a build
    # test status other than "pending", then the PR has not yet been informed
    # that the base HEAD has changed. Make sure the test gets a pending status,
    # and we will need to post on the repo that the base HEAD changed.
    #
    # If the base branch HEAD has changed since the last commit, but that 
    # commit test status is already pending, or the last commit doesn't have a
    # test status, then either it was already informed about the change or
    # there are no recently-completed tests to be out of date. Don't bother 
    # informing.
    if (
        (prConditions.baseCommitSha_lastTest is None or prConditions.baseHeadChanged)
        and not prConditions.newPR
        and "build" in prConditions.prev_test_statuses
        and not prConditions.prev_test_statuses["build"] == "pending"
    ):
        log.info(
            "The base branch HEAD has changed or we didn't know the base branch of the last test."
            " We need to reset the status of the build test and notify."
        )
        test_triggered["build"] = False
        test_statuses["build"] = "pending"
        test_status_exists["build"] = False
        doNotifyBaseHEADChanged = True
    elif prConditions.baseHeadChanged:
        log.info(
            "The build test status is not present or has already been reset. "
            "We will not notify about the changed HEAD."
        )
        doNotifyBaseHEADChanged = False

    # check if we've stalled
    # Tests that are not outdated because of a HEAD change, but were triggered
    # a long time ago and have never completed, are considered to have stalled.
    # Update the status to reflect this.
    tests_ = prConditions.prev_test_statuses.keys()
    for name in tests_:
        if name not in prConditions.legit_tests:
            continue
        log.info("Checking if %s has stalled...", name)
        log.info("Status is %s", prConditions.prev_test_statuses[name])
        if (
            (prConditions.prev_test_statuses[name] in ["running", "pending"])
            and (name in prConditions.test_triggered)
            and prConditions.test_triggered[name]
        ):
            test_runtime = (
                datetime.utcnow() - prConditions.commitStatusTime[name]
            ).total_seconds()
            log.info("  Has been running for %d seconds", test_runtime)
            if test_runtime > test_suites.get_stall_time(name):
                log.info("  The test has stalled.")
                test_triggered[name] = False  # the test may be triggered again.
                test_statuses[name] = "stalled"
                test_status_exists[name] = False
                stalled_jobs += [name]
                if name in prConditions.test_urls:
                    stalled_job_info += "\n- %s ([more info](%s))" % (
                        name,
                        prConditions.test_urls[name],
                    )
            else:
                log.info("  The test has not stalled yet...")
    # If we somehow got a test status indicating a completed build test, but
    # it's not connected to a commit, we can't say for sure whether the PR's 
    # tests are up-to-date or not. Reset the build test status to pending.
    if "build" in prConditions.legit_tests and prConditions.baseCommitSha_lastTest is None:
        if "build" in prConditions.prev_test_statuses and prConditions.prev_test_statuses["build"] in [
            "success",
            "finished",
            "error",
            "failure",
        ]:
            test_triggered["build"] = False
            test_statuses["build"] = "pending"
            test_status_exists["build"] = False
            log.info(
                "There's no record of when we last triggered the build test, "
                "and the status is not pending, so we are resetting the status."
            )
 # keep a track of our comments to avoid duplicate messages and spam.

    # # now we process comments
    # for comment in comments:
    #     if comment.user.login == config.main["bot"]["username"]:
    #         bot_comments += [comment.body.strip()]

    #     # Ignore all messages which are before last commit.
    #     if comment.created_at < last_commit_date:
    #         log.debug("IGNORE COMMENT (before last commit)")
    #         continue

    #     # neglect comments we've already responded to
    #     if last_time_seen is not None and (comment.created_at < last_time_seen):
    #         log.debug(
    #             "IGNORE COMMENT (seen) %s %s < %s",
    #             comment.user.login,
    #             str(comment.created_at),
    #             str(last_time_seen),
    #         )
    #         continue

    #     # neglect comments by un-authorised users
    #     if (
    #         comment.user.login not in authorised_users
    #         or comment.user.login == config.main["bot"]["username"]
    #     ):
    #         log.debug(
    #             "IGNORE COMMENT (unauthorised, or bot user) - %s", comment.user.login
    #         )
    #         continue

    #     for react in comment.get_reactions():
    #         if react.user.login == config.main["bot"]["username"]:
    #             log.debug(
    #                 "IGNORE COMMENT (we've seen it and reacted to say we've seen it) - %s",
    #                 comment.user.login,
    #             )

    #     reaction_t = None
    #     trigger_search, mentioned = None, None
    #     # now look for bot triggers
    #     # check if the comment has triggered a test
    #     try:
    #         trigger_search, mentioned = check_test_cmd_mu2e(
    #             comment.body, repo.full_name
    #         )
    #     except ValueError:
    #         log.exception("Failed to trigger a test due to invalid inputs")
    #         reaction_t = "-1"
    #     except Exception:
    #         log.exception("Failed to trigger a test.")

    #     tests_already_triggered = []

    #     if trigger_search is not None:
    #         tests, _, extra_env = trigger_search
    #         log.info("Test trigger found!")
    #         log.debug("Comment: %r", comment.body)
    #         log.debug("Environment: %s", str(extra_env))
    #         log.info("Current test(s): %r" % tests_to_trigger)
    #         log.info("Adding these test(s): %r" % tests)

    #         for test in tests:
    #             # check that the test has been triggered on this commit first
    #             if (
    #                 test in test_triggered
    #                 and test_triggered[test]
    #                 and test in test_statuses
    #                 and not test_statuses[test].strip()
    #                 in ["failed", "error", "success", "finished"]
    #             ):
    #                 log.debug("Current test status: %s", test_statuses[test])
    #                 log.info(
    #                     "The test has already been triggered for this ref. "
    #                     "It will not be triggered again."
    #                 )
    #                 tests_already_triggered.append(test)
    #                 reaction_t = "confused"
    #                 continue
    #             else:
    #                 test_triggered[test] = False

    #             if not test_triggered[test]:  # is the test already running?
    #                 # ok - now we can trigger the test
    #                 log.info(
    #                     "The test has not been triggered yet. It will now be triggered."
    #                 )

    #                 # update the 'state' of this commit
    #                 test_statuses[test] = "pending"
    #                 test_triggered[test] = True

    #                 # add the test to the queue of tests to trigger
    #                 tests_to_trigger.append((test, extra_env))
    #                 reaction_t = "+1"
    #     elif mentioned:
    #         # we didn't recognise any commands!
    #         reaction_t = "confused"

    #     if reaction_t is not None:
    #         # "React" to the comment to let the user know we have acknowledged their comment!
    #         comment.create_reaction(reaction_t)

    # trigger the 'default' tests if this is the first time we've seen this PR:
    # (but, only if they are in the Mu2e org)
    if trusted_user:
        if not_seen_yet and not dryRun and test_suites.AUTO_TRIGGER_ON_OPEN:
            for test in test_requirements:
                test_statuses[test] = "pending"
                test_triggered[test] = True
                if test not in [t[0] for t in tests_to_trigger]:
                    tests_to_trigger.append((test, {}))

    # now,
    # - trigger tests if indicated (for this specific SHA.)
    # - set the current status for this commit SHA
    # - apply labels according to the state of the latest commit of the PR
    # - make a comment if required
    jobs_have_stalled = False

    triggered_tests, extra_envs = list(zip(*tests_to_trigger)) or ([], [])
    for test, state in test_statuses.items():
        if test in legit_tests:
            labels.add(f"{test} {state}")

        if test in triggered_tests:
            log.info("Test will now be triggered! %s", test)
            # trigger the test in jenkins
            create_properties_file_for_test(
                test,
                repo.full_name,
                prId,
                git_commit.sha,
                master_commit_sha,
                extra_envs[triggered_tests.index(test)],
            )
            if not dryRun:
                if test == "build":
                    # we need to store somewhere the master commit SHA
                    # that we merge into for the build test (for validation)
                    # this is overlapped with the next, more human readable message
                    last_commit.create_status(
                        state="success",
                        target_url="https://github.com/mu2e/%s" % repo.name,
                        description="Last test triggered against %s"
                        % master_commit_sha[:8],
                        context="mu2e/buildtest/last",
                    )

                last_commit.create_status(
                    state="pending",
                    target_url="https://github.com/mu2e/%s" % repo.name,
                    description="The test has been triggered in Jenkins",
                    context=test_suites.get_test_alias(test),
                )
            log.info(
                "Git status created for SHA %s test %s - since the test has been triggered.",
                git_commit.sha,
                test,
            )
        elif state == "pending" and test_status_exists[test]:
            log.info(
                "Git status unchanged for SHA %s test %s - the existing one is up-to-date.",
                git_commit.sha,
                test,
            )
        elif state == "stalled" and not test_status_exists[test]:
            log.info("Git status was pending, but the job has stalled.")
            last_commit.create_status(
                state="error",
                target_url="https://github.com/mu2e/%s" % repo.name,
                description="The job has stalled on Jenkins. It can be re-triggered.",
                context=test_suites.get_test_alias(test),
            )
            jobs_have_stalled = True

        elif (
            state == "pending"
            and not test_triggered[test]
            and not test_status_exists[test]
        ):
            log.debug(test_status_exists)
            log.info(
                "Git status created for SHA %s test %s - since there wasn't one already."
                % (git_commit.sha, test)
            )
            labels.add(f"{test} {state}")
            # indicate that the test is pending but
            # we're still waiting for someone to trigger the test
            if not dryRun:
                last_commit.create_status(
                    state="pending",
                    target_url="https://github.com/mu2e/%s" % repo.name,
                    description="This test has not been triggered yet.",
                    context=test_suites.get_test_alias(test),
                )
        # don't do anything else with commit statuses
        # the script handler that handles Jenkins job results will update the commits accordingly

    # check if labels have changed
    labelnames = {x.name for x in issue.labels if "unrecognised" not in x.name}
    if labelnames != labels:
        if not dryRun:
            issue.edit(labels=list(labels))
        log.debug("Labels have changed to: %s", ", ".join(labels))

    # check label colours
    try:
        for label in issue.labels:
            if label.color == "ededed":
                # the label color isn't set
                for labelcontent, col in state_labels_colors.items():
                    if labelcontent in label.name:
                        label.edit(label.name, col)
                        break
    except Exception:
        log.exception("Failed to set label colours!")

    # construct a reply if tests have been triggered.
    tests_triggered_msg = ""
    already_running_msg = ""
    commitlink = git_commit.sha

    if len(tests_to_trigger) > 0:
        if len(tests_already_triggered) > 0:
            already_running_msg = "(already triggered: %s)" % ",".join(
                tests_already_triggered
            )

        tests_triggered_msg = TESTS_TRIGGERED_CONFIRMATION.format(
            commit_link=commitlink,
            test_list=", ".join(list(zip(*tests_to_trigger))[0]),
            tests_already_running_msg=already_running_msg,
            build_queue_str=get_build_queue_size(),
        )

    # decide if we should issue a comment, and what comment to issue
    if not_seen_yet:
        log.info("First time seeing this PR - send the user a salutation!")
        if not dryRun:
            post_on_pr(
                issue,
                PR_SALUTATION.format(
                    pr_author=pr_author,
                    changed_folders="\n".join(
                        ["- %s" % s for s in modified_top_level_folders]
                    ),
                    tests_required=", ".join(test_requirements),
                    watchers=watcher_text,
                    auth_teams=", ".join(["@Mu2e/%s" % team for team in authed_teams]),
                    tests_triggered_msg=tests_triggered_msg,
                    non_member_msg="" if trusted_user else PR_AUTHOR_NONMEMBER,
                    base_branch=pr.base.ref,
                ),
                bot_comments,
            )

    elif len(tests_to_trigger) > 0:
        # tests were triggered, let people know about it
        if not dryRun:
            post_on_pr(issue, tests_triggered_msg, bot_comments)

    elif len(tests_to_trigger) == 0 and len(tests_already_triggered) > 0:
        if not dryRun:
            post_on_pr(
                issue,
                TESTS_ALREADY_TRIGGERED.format(
                    commit_link=commitlink,
                    triggered_tests=", ".join(tests_already_triggered),
                ),
                bot_comments,
            )

    if jobs_have_stalled and not dryRun:
        post_on_pr(
            issue,
            JOB_STALL_MESSAGE.format(
                joblist=", ".join(stalled_jobs), info=stalled_job_info
            ),
            bot_comments,
        )
    if base_branch_HEAD_changed and not dryRun and not len(tests_to_trigger) > 0:
        post_on_pr(
            issue,
            BASE_BRANCH_HEAD_CHANGED.format(
                base_ref=pr.base.ref, base_sha=master_commit_sha
            ),
            bot_comments,
        )
    if "build" in test_status_exists:
        if future_commit and not test_status_exists["build"] and not dryRun:
            post_on_pr(
                issue,
                f":memo: The latest commit by @{git_commit.committer.name} is "
                f"timestamped {future_commit_timedelta_string} in the future. "
                "Please check that the date and time is set correctly when creating new commits.",
                bot_comments,
            )
