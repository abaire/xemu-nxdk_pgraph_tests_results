# ruff: noqa: S607 Starting a process with a partial executable path

import logging
import os
import subprocess

logger = logging.getLogger(__name__)


def git(*args):
    return subprocess.check_output(["git", *list(args)]).decode().strip()


def sync_version(version: str, remote_branches):
    branch_name = f"archive/{version}"
    print(f"Checking {version}...")

    try:
        # Construct a git tree for the archive branch
        # We include: results/<v>, compare-results/<v> (if exists), and known_issues.json
        tree_entries = []

        # Add results directory
        res_tree = git("rev-parse", f"HEAD:results/{version}")
        tree_entries.append(f"040000 tree {res_tree}\tresults/{version}")

        # Add compare-results if they exist
        try:
            comp_tree = git("rev-parse", f"HEAD:compare-results/{version}")
            tree_entries.append(f"040000 tree {comp_tree}\tcompare-results/{version}")
        except Exception:
            logger.exception("No diffs yet")

        # Add global known_issues.json
        issues_blob = git("rev-parse", "HEAD:results/known_issues.json")
        tree_entries.append(f"100644 blob {issues_blob}\tresults/known_issues.json")

        # Create the tree object
        tree_hash = subprocess.check_output(["git", "mktree"], input="\n".join(tree_entries).encode()).decode().strip()

        # Create a commit object
        new_commit = git("commit-tree", tree_hash, "-m", f"Archive results for {version}")

        # Check if remote branch exists and matches
        remote_ref = f"refs/remotes/origin/{branch_name}"
        if branch_name in remote_branches:
            remote_sha = git("rev-parse", remote_ref)
            # Compare the tree of the remote commit with our new tree
            remote_tree = git("rev-parse", f"{remote_sha}^{{tree}}")
            if remote_tree == tree_hash:
                print(f"  [S] {version} is up to date.")
                return

        # Push the new commit to the archive branch
        print(f"  [P] Updating {branch_name}...")
        git("push", "origin", f"{new_commit}:refs/heads/{branch_name}", "--force")

    except Exception:
        logger.exception("  [!] Failed to sync %s", version)


if __name__ == "__main__":
    # Get all remote branches to avoid unnecessary ls-remote calls
    branches = git("branch", "-r").split()
    remote_archive_branches = [b.split("origin/")[1] for b in branches if "origin/archive/" in b]

    versions = [d for d in os.listdir("results") if os.path.isdir(os.path.join("results", d))]
    for v in versions:
        sync_version(v, remote_archive_branches)
