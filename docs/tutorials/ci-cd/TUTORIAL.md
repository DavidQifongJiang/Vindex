# Learn CI/CD by shipping a video chunk planner

You will take a small Python application from a local test to a tested Docker release, then promote the same release through staging and an approval gate. You will deliberately introduce bugs, read failure logs, fix a pull request, detect an unhealthy deployment, and roll back. An optional final lab runs your tested release on a separate EC2 instance.

Work through one lab at a time. Before each command, predict the result. After it runs, explain what happened without looking at the command. The checkpoints are the completion criteria; watching a green checkmark is only part of the exercise.

Estimated practice time is 4–6 hours across several sessions, plus optional EC2 setup. This is an estimate for a first pass, not a course requirement. The first two labs need Python only. Later labs need Git, a GitHub account, and Docker Desktop using Linux containers. Python 3.11 is used in CI and Docker; the app also runs on newer local Python versions. There are no Python packages to install.

The starter is a teaching app, not Vindex itself. It calculates how many five-minute chunks a video needs: 300 seconds needs one chunk; 301 needs two. The HTTP server and deliberate failure switches are for practice. You will publish only the practice files to your own practice repository.

## 0 Understand the route your code will take

```text
Your laptop: edit -> test -> commit -> push
                            |
GitHub: PR -> test job -> merge to main
                            |
GitHub runner: test -> build ONE image -> save release artifact
                                             |
                                  staging deployment + smoke test
                                             |
                                         approval
                                             |
                                production-demo + smoke test
                                             |
You: download SAME image -> persistent local Docker deployment
                                             |
                                  optional practice EC2 deployment
```

Continuous integration (CI) means integrating changes frequently and running automated checks. Continuous delivery keeps a tested release ready for a human decision to deploy. Continuous deployment automates the production decision after the required checks. The workflow file is an executable description of your process, not the definition of CI by itself.

| Word | What it means in this lab |
| --- | --- |
| Commit | A recorded version of the source code, identified by a Git SHA |
| Trigger | An event such as opening a pull request or pushing to main |
| Workflow | The YAML file describing the automation |
| Job | A group of steps on one runner; different jobs normally have separate machines |
| Step | One command or reusable action inside a job |
| Runner | The computer executing a job; it is not your laptop or your EC2 instance |
| Exit code | Zero means success; a nonzero value makes a normal command step fail |
| Image | The packaged application and runtime |
| Container | A running instance of an image |
| Artifact | A file saved from a workflow, such as this lab's Docker image archive |
| Registry | A service for storing and distributing images, such as GHCR or ECR |
| Environment | A deployment target and its configuration; GitHub also uses this name for protection rules |
| Smoke test | A short check of a running release's essential behavior |

All local command blocks marked `powershell` run in Windows PowerShell. Workflow `run:` blocks execute Bash on Ubuntu runners. The optional EC2 blocks marked `bash` execute on Linux. Do not mix their environment-variable syntax: PowerShell uses `$env:NAME`; Bash uses `$NAME` and `export NAME=value`.

The practice workflows use readable major-version action tags. A production repository should review and pin third-party actions to verified commit SHAs and update those pins deliberately. The lab uses only GitHub-maintained actions and Docker CLI commands. [GitHub Actions concepts](https://docs.github.com/en/actions/get-started/understand-github-actions).

## 1 Run something before automating it

Create your independent practice folder. The helper refuses to overwrite an existing folder and refuses to create it inside Vindex.

```powershell
python "C:\Users\ASUS\Desktop\2025\gongzuo\Vindex\docs\tutorials\ci-cd\create_practice.py" "$HOME\Desktop\cicd-practice"
Set-Location "$HOME\Desktop\cicd-practice"
python --version
python -m unittest discover -s tests -v
$LASTEXITCODE
```

Expect **10 tests**, `OK`, and exit code `0`. Your local Python may be newer than 3.11; CI will select 3.11 explicitly.

Read `app.py`, especially `chunk_count`. Open `tests/test_app.py`. The test for 301 seconds checks the boundary most likely to reveal an incomplete final chunk. Tests also cover invalid durations, the HTTP contract, release identity, and the difference between alive and ready.

Start the app in terminal A:

```powershell
python app.py
```

In a second PowerShell terminal, enter the practice folder and send requests:

```powershell
Set-Location "$HOME\Desktop\cicd-practice"
Invoke-RestMethod 'http://127.0.0.1:18080/health'
Invoke-RestMethod 'http://127.0.0.1:18080/plan?seconds=301'
python scripts/smoke.py http://127.0.0.1:18080 --expected-release local
```

Expect `chunks: 2` and `SMOKE PASSED`. Stop the server with Ctrl+C in terminal A. Unit tests launched their own temporary server on a free port, so they did not require this manually started server.

**Checkpoint:** What does `python -m unittest` do? Which test catches a missing partial chunk? Why does exit code zero matter to automation?

## 2 Make a test fail and repair the code

In `app.py`, change exactly this line:

```python
return math.ceil(seconds / chunk_seconds)
```

to this deliberate bug:

```python
return math.floor(seconds / chunk_seconds)
```

Predict the answer for 301 seconds. Run:

```powershell
python -m unittest discover -s tests -v
$LASTEXITCODE
```

Expect a nonzero exit code and failures showing that a partial chunk was lost. Read the first failure's test name, expected value, actual value, and source line. Other failures may share the same cause.

Repair `floor` back to `ceil`, then rerun the tests. Keep the expectations at two chunks for 301 seconds. Changing the test to accept the bug would remove the behavior you intended to protect.

**Checkpoint:** Explain how the same command can become a CI gate. A human reads the traceback; the runner uses the exit code.

## 3 Create your practice Git repository

Run these from the copied practice folder, after the code is fixed:

```powershell
git init -b main
git status
git add app.py tests scripts Dockerfile compose.yaml .dockerignore .gitignore .gitattributes README.md solutions
git diff --cached --stat
git commit -m "Add working chunk planner"
```

If Git asks for identity, configure your name and email for this practice repository with `git config user.name` and `git config user.email`, then retry the commit. Use your chosen GitHub no-reply email if that is your preference.

In GitHub, create an **empty practice repository** named `cicd-practice`. Do not add a README or license during creation because your local first commit already contains the files. A public practice repository makes the later required-reviewer lab available on current GitHub Free plans. A private repository is also usable, but approval and branch-protection features depend on your plan. Only the practice app belongs in this repository.

Replace `YOUR-USER` in the following URL with your GitHub username:

```powershell
git remote add origin https://github.com/YOUR-USER/cicd-practice.git
git push -u origin main
```

Follow Git's browser authentication prompt if shown. A GitHub account password is not an HTTPS Git credential. The Git Credential Manager normally handles this on Windows.

Visit the repository and confirm you see `app.py`, `Dockerfile`, and `tests/`. The files in `solutions/` are reference answers; GitHub does not execute them from that directory.

**Checkpoint:** Explain the difference between saving a file, committing it, and pushing it. Which of these sends the code to GitHub?

## 4 Write and run your first CI workflow

Create `.github/workflows/ci.yml` in your practice repository. Type the following small workflow so you understand the pieces. A matching reference file is available at `solutions/ci.yml`.

```yaml
name: Practice CI
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
  workflow_dispatch:
permissions:
  contents: read
jobs:
  test:
    name: test
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@v7
        with:
          persist-credentials: false
      - uses: actions/setup-python@v7
        with:
          python-version: '3.11'
      - run: |
          python -m compileall -q app.py scripts tests
          python -m unittest discover -s tests -v
```

`on` chooses the events. `jobs.test` defines one job. `runs-on` chooses a fresh Ubuntu runner. `checkout` downloads the selected source revision. `setup-python` selects a Python version; the `v7` in the action name is the action's version, not Python's version. `run` executes ordinary shell commands. `permissions` limits the workflow token to reading repository contents. A compile check catches syntax problems; tests check behavior.

If you want to install the reference instead of typing it:

```powershell
New-Item -ItemType Directory -Force .github/workflows
Copy-Item solutions/ci.yml .github/workflows/ci.yml
```

Commit and push:

```powershell
git add .github/workflows/ci.yml
git commit -m "Run checks with GitHub Actions"
git push
```

Open **Actions → Practice CI → the new run → test**. Expand checkout, setup, and test steps. Find the same 10-test result you saw locally. The runner has none of your uncommitted local changes and does not inherit your local Python installation.

**Checkpoint:** Identify the trigger, workflow, job, runner, action, and command in your own file. Where did the tests actually execute? [Python workflow documentation](https://docs.github.com/en/actions/tutorials/build-and-test-code/python).

## 5 Use a failing pull request as a gate

Create a branch:

```powershell
git switch -c break-chunk-rounding
```

Introduce the `ceil` to `floor` bug again. Commit and push it:

```powershell
git add app.py
git commit -m "Demonstrate a rounding regression"
git push -u origin break-chunk-rounding
```

Open a pull request from that branch into `main` in GitHub. This branch push by itself does not match `push.branches: [main]`; opening the pull request triggers the `pull_request` event. Find the failed `test` check on the PR and follow its log link.

Configure a rule for `main` under the repository's branch protection or ruleset settings. Require a pull request and require the status check named **test** to pass. Select the check actually shown by your first run. Make the rule active. Do not require an approving PR review for this solo exercise; you cannot approve your own PR. Required status checks and reviewer approvals are different gates. Administrators may have bypass permissions, so observe the actual merge controls rather than assuming a red check automatically blocks every merge.

If your plan does not expose the required rule, you can still inspect the failure, but mark enforcement as unavailable rather than claiming GitHub blocked the merge. [Protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches).

Repair `floor` to `ceil` on the same branch, rerun tests locally, commit, and push:

```powershell
python -m unittest discover -s tests -v
git add app.py
git commit -m "Restore partial chunk rounding"
git push
```

The existing PR gets a new run. After it passes, merge the PR in GitHub. Synchronize your local main branch:

```powershell
git switch main
git pull --ff-only
```

**Checkpoint:** Explain why a failed CI run and a blocked merge are separate things. Explain why fixing the existing PR is different from opening a new one.

## 6 Package and deploy a Docker image locally

Start Docker Desktop with Linux containers. Starting Docker may also resume other containers configured to restart; inspect Docker Desktop if you have other projects. Confirm the engine is available:

```powershell
docker version
docker compose version
```

You should see both client and server information. If the Linux engine pipe is missing, the Docker engine is not ready. Do not debug application code for that error.

Read the practice `Dockerfile`. It starts from Python 3.11, copies only `app.py`, records a release identifier, runs as a non-root user, and probes `/ready`. The standard-library server is enough to demonstrate packaging; Vindex's real image additionally builds React and installs FFmpeg and model libraries.

Build the first release:

```powershell
docker build --build-arg RELEASE_SHA=good-v1 -t cicd-lab:good-v1 .
$env:LAB_IMAGE = 'cicd-lab:good-v1'
$env:LAB_ENV = 'staging'
$env:LAB_PORT = '18080'
docker compose -p cicd-lab-staging up -d --wait --wait-timeout 45
python scripts/smoke.py http://127.0.0.1:18080 --expected-release good-v1
docker compose -p cicd-lab-staging ps
docker compose -p cicd-lab-staging logs --tail 20
```

Expect `SMOKE PASSED`, release `good-v1`, environment `staging`, and a healthy container. If port 18080 is busy, stop the Python server from lab 1 or use another `LAB_PORT` and update the smoke URL.

`-p cicd-lab-staging` gives this Compose deployment a distinct project name. Port 18080 on your machine forwards to 8080 inside the container. The host binding is loopback, so this lab is not exposed on your network. `--wait` waits for the healthcheck, not just the existence of a process. `EXPOSE 8080` documents the container port; the Compose port mapping makes it reachable on your host.

Edit the local `app.py` without rebuilding. The running container keeps using the copy inside its image. Restore your edit before continuing. This demonstrates why a source change does not automatically change a running release.

**Checkpoint:** Explain source versus image versus container. Why did changing a local file not change the container? [Docker Compose startup behavior](https://docs.docker.com/compose/how-tos/startup-order/).

## 7 Deploy a bad release and roll back

Keep `good-v1` in your local image store. Build another image with readiness deliberately broken. Its chunk-count code remains correct:

```powershell
docker build --build-arg RELEASE_SHA=bad-v2 --build-arg FAIL_READY=1 -t cicd-lab:bad-v2 .
$env:LAB_IMAGE = 'cicd-lab:bad-v2'
docker compose -p cicd-lab-staging up -d --wait --wait-timeout 45
$LASTEXITCODE
```

Expect the deployment command to fail because the container becomes unhealthy. The failed command does not perform a rollback for you. Inspect it:

```powershell
Invoke-RestMethod 'http://127.0.0.1:18080/health'
curl.exe -i http://127.0.0.1:18080/ready
docker compose -p cicd-lab-staging ps
docker compose -p cicd-lab-staging logs --tail 30
```

`/health` says the process is alive; `/ready` returns HTTP 503. Unit tests can pass while a release fails after deployment because configuration and packaging are additional failure sources.

Roll back by selecting the previous retained image:

```powershell
$env:LAB_IMAGE = 'cicd-lab:good-v1'
docker compose -p cicd-lab-staging up -d --wait --wait-timeout 45
python scripts/smoke.py http://127.0.0.1:18080 --expected-release good-v1
```

This lab uses a stateless service, so rollback is replacing the container with the earlier image. Vindex also has a database: an incompatible schema migration can make an old image unusable. Application rollback does not automatically reverse schema or data changes. Real releases often use backward-compatible expand-and-contract migrations so old and new code can coexist during rollout.

Stop only this practice project when you are done:

```powershell
docker compose -p cicd-lab-staging down
```

**Checkpoint:** Did Compose automatically restore `good-v1`? Which evidence showed the new deployment was unhealthy? Why would database state make Vindex's rollback harder?

## 8 Build once and promote through GitHub Actions

Your next workflow has four jobs: `test`, `build`, `staging`, and `production-demo`. Read `solutions/pipeline.yml` before copying it. `needs` defines which jobs must succeed first. PRs run tests; main-branch releases build an image. Each job gets a fresh runner, so the built image must be saved and transferred as an artifact.

Set up the approval gate **before** activating the workflow:

1. In your practice repository, open Settings → Environments.
2. Create an environment named `production-demo`.
3. Enable Required reviewers and choose yourself for this solo exercise.
4. Leave Prevent self-review disabled for the exercise so you can approve a run you triggered. A team would use an independent reviewer.
5. Restrict deployment branches to `main` and save the rules.

The YAML word `environment` alone does not enforce approval. A missing environment can be created without protection rules. Required reviewers on current Free/Pro/Team plans are limited to public repositories; check your repository's actual settings. If unavailable, use a public disposable practice repository or mark this lab's approval portion unavailable. [Environment rules](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments).

Replace your existing workflow, keeping the same file path:

```powershell
Copy-Item solutions/pipeline.yml .github/workflows/ci.yml
git switch -c add-release-pipeline
git add .github/workflows/ci.yml
git commit -m "Build and promote a tested release"
git push -u origin add-release-pipeline
```

Open and merge this PR after its `test` check passes. Do not add this as a second CI file; replacing the first avoids duplicate workflows and confusing status checks. On the main-branch run, observe:

1. **test** runs the familiar commands.
2. **build** builds `cicd-lab:<commit SHA>`, records its image ID, and saves it as `image.tar` with the Compose file, smoke test, and checksum manifest.
3. **staging** downloads that artifact on another runner, verifies checksums and image identity, loads it, starts a container, and checks both behavior and the exact release SHA.
4. **production-demo** waits for your environment approval. Inspect the staging result, then choose Review deployments and approve it.
5. The approved job loads the **same image**; it does not build another image. It changes `LAB_ENV`, runs the smoke check, and cleans up.

The image tag contains the commit SHA to identify its source. A tag can be moved, so it is not an immutability guarantee by itself. This workflow also records and verifies the image ID. Checksums detect file corruption; they do not independently prove who produced an artifact. Here, the workflow trusts the artifact from its own successful build job.

Both GitHub deployments are real containers on temporary runners. They are removed by cleanup steps, and the runners are discarded. `localhost` in their logs refers to the runner, not your laptop. You cannot visit those runner-local URLs after the job ends. The persistent local deployment comes in lab 9.

The `concurrency` setting reduces overlapping releases on a branch. It does not turn GitHub into a strict first-in-first-out queue; pending runs may be superseded. The cleanup step uses `always()`; diagnostic logs use `failure()`. Neither makes a failed deployment successful. [Workflow artifacts](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts).

**Failure exercise:** On a new PR branch, change the Dockerfile's default `ARG FAIL_READY=0` to `ARG FAIL_READY=1`. Unit tests still pass. Merge only in this practice repo and watch main's staging fail. The approval/deployment job must not run. Fix the default in another PR, merge, and verify the entire pipeline passes. This shows why a green unit test job is not a green release.

**Checkpoint:** Why does staging need `docker load`? Which job created the artifact? What makes production-demo wait? Which checks stop a stale but healthy image from passing?

## 9 Run the GitHub-built release on your own computer

Open a successful **Practice pipeline** run in GitHub. In its Artifacts section, download `release-<commit SHA>` and extract its contents into the `release` folder inside your practice directory. The folder should directly contain `image.tar`, `image-ref.txt`, `image-id.txt`, `commit.txt`, `compose.yaml`, `smoke.py`, and `SHA256SUMS`.

From your practice directory, verify the downloaded files:

```powershell
Get-Content release/SHA256SUMS | ForEach-Object {
    $expectedHash, $releaseFile = $_ -split '  ', 2
    $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path 'release' $releaseFile)).Hash.ToLower()
    if ($actualHash -ne $expectedHash) { throw "Checksum mismatch: $releaseFile" }
}
docker load --input release/image.tar
$env:LAB_IMAGE = (Get-Content release/image-ref.txt -Raw).Trim()
$expectedImageId = (Get-Content release/image-id.txt -Raw).Trim()
$actualImageId = (docker image inspect $env:LAB_IMAGE --format '{{.Id}}').Trim()
if ($actualImageId -ne $expectedImageId) { throw 'Wrong loaded image' }
$releaseSha = (Get-Content release/commit.txt -Raw).Trim()
$env:LAB_ENV = 'local-production'
$env:LAB_PORT = '18081'
docker compose -f release/compose.yaml -p cicd-lab-production up -d --wait --wait-timeout 45
python release/smoke.py http://127.0.0.1:18081 --expected-release $releaseSha
```

Open `http://127.0.0.1:18081/plan?seconds=301`. Match the returned release to the GitHub commit. Stop the command terminal: the container keeps running because Compose started it in detached mode. It remains available while Docker runs, until you stop it. The lab deliberately does not configure automatic restart after a Docker/host restart.

You have promoted the already tested image to a persistent deployment without rebuilding source on the destination. Keep a previous successful image and its release files if you want to repeat rollback with actual Git SHA releases.

Clean up only this deployment:

```powershell
docker compose -f release/compose.yaml -p cicd-lab-production down
```

**Checkpoint:** Why is deploying this saved image stronger evidence than running a new `docker build` on your laptop? What does an artifact provide that a workflow log does not?

## 10 Learn variables, secrets, registries and permissions

`LAB_ENV` is ordinary configuration. The release SHA is public version information. Neither needs to be a secret. An SSH private key, a password, or a cloud credential is a secret. Do not put secrets in source, Docker build arguments, artifacts, or printed logs. A Docker image can retain build-time data even if a later step removes a file.

The automatically provided `GITHUB_TOKEN` has permissions scoped to a workflow. Our test pipeline requests only `contents: read`. Publishing an image needs additional `packages: write`. A pull request test should not receive deployment credentials. In particular, do not switch to `pull_request_target` to run untrusted PR code with privileged credentials.

Artifacts and registries serve different roles. GitHub artifacts store files for a workflow and expire after their retention period (this lab sets seven days). GHCR or ECR distributes images by tags and digests. A registry digest such as `image@sha256:...` selects exact registry content. It is distinct from the local image ID and from the checksum of the saved tar file.

The optional `solutions/publish.yml` publishes a **previously tested artifact**, rather than rebuilding. It requires a successful Practice pipeline run from main in the same repository and verifies the selected run and image identity. For this exercise:

1. Create a GitHub environment named `registry-publish`, restrict it to main, and configure a required reviewer as in lab 8.
2. Copy `solutions/publish.yml` to `.github/workflows/publish.yml` on a new branch, create a PR, and merge after tests pass.
3. Find a successful Practice pipeline run; its numeric run ID appears in the URL after `/actions/runs/`.
4. Open Actions → Publish tested practice image → Run workflow on main and enter that run ID. Approve the environment job.
5. Inspect the publish logs and the repository/account Packages page for `ghcr.io/<lowercase-owner>/cicd-practice:<SHA>` and its registry digest.

This publishes the practice image under your account. The workflow does not change package visibility. A private package requires authentication on a machine pulling it; public packages allow anonymous pulls. To practice without registry permissions, lab 9's artifact path remains sufficient. No personal access token is needed inside the publishing workflow; it uses `GITHUB_TOKEN`. [Publishing images](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images).

For AWS automation, learn GitHub OIDC next: a trusted workflow exchanges its identity for a short-lived AWS role session, with a trust policy restricted to your repository and environment. This avoids storing a permanent AWS access key in the workflow. [AWS OIDC guide](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws).

**Checkpoint:** Distinguish a variable, a secret, a job output, a cache, and a release artifact. A cache speeds up reconstructible work; it is not the authoritative release you promote.

## 11 Deploy the same release to a practice EC2 instance

This optional lab adds a persistent remote machine. Use a separate practice Ubuntu EC2 instance, not the Vindex host. AWS charges depend on the instance, disks, addresses, and account; check the console estimate before launching it.

Provision an Ubuntu instance with a key pair you control. Allow SSH only from your current public IP. The app will remain on loopback and you will reach it through an SSH tunnel, so no public application port is required for this exercise. Install Docker Engine and the Compose plugin using the [official Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/). Use an EC2 instance architecture compatible with the image built on the default x86-64 Ubuntu runner; an ARM instance would require a matching or multi-platform image.

This lab uses `sudo docker` on the remote host, avoiding an assumption that your SSH user already belongs to the Docker group. Verify these on EC2:

```bash
sudo docker version
sudo docker compose version
python3 --version
mkdir -p "$HOME/cicd-practice/release"
```

On Windows, point these variables at your own key and practice instance. Replace the example values before executing:

```powershell
$practiceKey = "$HOME\.ssh\YOUR-PRACTICE-KEY.pem"
$practiceHost = 'ubuntu@YOUR-PRACTICE-PUBLIC-IP'
ssh -i $practiceKey $practiceHost
```

Verify the SSH host fingerprint through a trusted AWS console/session before accepting it. Return to your local PowerShell terminal with `exit`. From the practice folder, transfer the release you downloaded in lab 9:

```powershell
scp -i $practiceKey release/image.tar release/image-ref.txt release/image-id.txt release/commit.txt release/compose.yaml release/smoke.py release/SHA256SUMS "${practiceHost}:cicd-practice/release/"
ssh -i $practiceKey $practiceHost
```

Now execute on **EC2 Bash**, not PowerShell:

```bash
cd "$HOME/cicd-practice/release"
sha256sum -c SHA256SUMS
sudo docker load --input image.tar
export LAB_IMAGE="$(cat image-ref.txt)"
export LAB_ENV=practice-ec2
export LAB_PORT=18081
test "$(sudo docker image inspect "$LAB_IMAGE" --format '{{.Id}}')" = "$(cat image-id.txt)"
sudo env LAB_IMAGE="$LAB_IMAGE" LAB_ENV="$LAB_ENV" LAB_PORT="$LAB_PORT" \
  docker compose -p cicd-lab-ec2 up -d --wait --wait-timeout 45
python3 smoke.py http://127.0.0.1:18081 --expected-release "$(cat commit.txt)"
```

Proceed to the deployment only after the checksum and image-ID checks succeed. On a shell, unlike a configured CI step, an earlier failing command does not necessarily stop you from typing the next one. A deployment script should explicitly stop on failure.

From another local PowerShell terminal, keep a tunnel open:

```powershell
ssh -i $practiceKey -N -L 18082:127.0.0.1:18081 $practiceHost
```

Open `http://127.0.0.1:18082/plan?seconds=301` on your laptop. The request travels through SSH to the container on EC2. Match the release SHA to GitHub. Closing the tunnel ends local access, but the EC2 container keeps running while its host and Docker run.

This is a manual promotion of a CI-built release. The provided GitHub workflow does **not** SSH into EC2 or automate this persistent deployment. To automate that next, use a protected deployment job with OIDC, restricted AWS permissions, and a transport such as SSM to run the same verify/load/start/check steps. You would also need release concurrency, rollback, and deployment status reporting. The temporary production-demo job is not evidence that this remote automation exists.

Clean up the practice container on EC2:

```bash
cd "$HOME/cicd-practice/release"
sudo env LAB_IMAGE="$(cat image-ref.txt)" LAB_PORT=18081 \
  docker compose -p cicd-lab-ec2 down
```

Close the SSH tunnel and stop or terminate the practice EC2 resources through AWS when finished. Review retained disks and addresses as well; stopping a container is not stopping the billed instance.

**Checkpoint:** Identify where the image was built, where it was tested, and where it now runs. What did SSH transport? Why did no `git pull` or `docker build` run on EC2?

## 12 Apply the lessons to Vindex

Open Vindex's existing `.github/workflows/ci.yml` and annotate it in your own words. It has a smoke job and a separate Kafka integration job. Each job gets its own runner and service environment. The smoke job installs dependencies, compiles Python, runs backend tests, checks backup/restore behavior, builds the frontend, and validates Compose. These are configured checks; a workflow file by itself is not evidence that a particular commit passed.

Vindex's current documented deployment still retrieves source and builds on EC2. It does not have this lab's artifact-promotion workflow. A future Vindex delivery pipeline would need to account for:

- Frontend Cognito variables embedded at image build time, and backend configuration supplied at runtime.
- The API, search worker, playback worker, dispatcher, maintenance, and optional workers using a compatible release.
- Upload admission, worker draining, Postgres backups, and migration ordering before starting workers against a new schema.
- The database and S3 retaining state across container replacements.
- Readiness and actual user-journey checks: login, upload, processing, playback, search, and ownership.
- Compatibility-aware rollback, including jobs/messages already in flight and database schema changes.
- Optional search and Kafka paths enabled only by explicit configuration and their own acceptance evidence.

Draw your own small map from memory with five branches: **Trigger, Check, Package, Promote, Verify**. Under each, write which machine performs the work and what failure prevents the next step. Add **Rollback** beside Promote. Keep the stored image identity and migration compatibility visible on that branch.

## Troubleshoot from evidence

| Symptom | Check first |
| --- | --- |
| No Actions run appeared | Is the YAML under `.github/workflows/` in the practice repo? Did the event match `on`? Are Actions enabled? |
| A feature-branch push did nothing | This workflow runs branch changes through a PR; `push` is restricted to main. |
| Tests pass locally and fail in CI | Compare committed files, Python version, OS paths, case sensitivity, and environment assumptions. |
| Build or deploy job is skipped | Check `needs`, the triggering event, and the main-branch condition. |
| Approval never appeared | Check that the environment exists, required reviewers are saved, and staging succeeded. |
| You cannot approve the deployment | Check the reviewer list, plan availability, and Prevent self-review setting. |
| Docker pipe missing | Start Docker Desktop's Linux engine and wait for server information in `docker version`. |
| Port is already allocated | Stop the known practice server or select another lab port and update the smoke URL. |
| App is alive but deployment failed | Inspect `/ready`, container health, and the smoke test's expected release. |
| Artifact is missing | Select the successful main-branch release run, check its name, retention, and permissions. |
| Registry push is denied | Check `packages: write`, repository/package access, organization policy, and lowercase image naming. |
| Changing source has no effect | Containers run the image's copied code; build a new release and recreate the container. |

## Check your understanding

Answer before opening the explanations:

1. A test command exits with status 1. Why should the image build not run?
2. Why must a new job download an artifact even if a previous job created it?
3. How can two deployments use the same image but different configuration?
4. Why is a green `/health` response insufficient?
5. Why is `environment: production-demo` alone not an approval gate?
6. What is the difference between source SHA, image tag, image ID, registry digest, and archive checksum?
7. What can an image rollback fail to undo in Vindex?
8. When would this tutorial become continuous deployment rather than approval-based delivery?

<details markdown="1"><summary>Compare your answers</summary>

1. The nonzero exit marks the test job failed. The dependent build requires that job to succeed.
2. Jobs usually run on separate fresh machines. Artifacts transfer persistent outputs between them.
3. Configuration such as `LAB_ENV` is supplied when the container starts. The packaged application bytes stay the same.
4. The process may respond while a dependency or required behavior is unavailable, or while the wrong release is running.
5. The GitHub environment needs an actual configured protection rule. An unprotected environment runs immediately.
6. Source SHA identifies source; tag is a movable name; image ID identifies a local image configuration; registry digest identifies registry content; archive checksum verifies the saved file bytes.
7. Database schema/data changes, deleted objects, and already performed external side effects are not reversed by changing an application image.
8. A persistent production deployment would run automatically after all required checks, without a human release decision. Removing the demo approval only automates the temporary demonstration.

</details>

## Record what you actually completed

Use this as your own evidence log. Keep failing runs as learning evidence.

| Lab | Evidence to save |
| --- | --- |
| Local test failure | Failing test name and the code change that caused it |
| CI | URL of a successful workflow run |
| PR gate | URL of the failed PR check and the later successful run |
| Docker | `good-v1` image ID and successful smoke output |
| Rollback | Bad release's 503 response, followed by good release's SHA |
| Promotion | Artifact name, image ID, staging result, and approval record |
| Persistent local release | Response containing the same source SHA as the workflow |
| Registry, optional | Published image reference and registry digest |
| EC2, optional | Matching remote release SHA and cleanup record |

Useful references: [GitHub Skills](https://learn.github.com/skills), [workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax), [Docker build](https://docs.docker.com/build/), [AWS delivery concepts](https://aws.amazon.com/devops/continuous-delivery/). Revisit these when a specific lab gives you a question; completing the exercises is the primary learning activity.
