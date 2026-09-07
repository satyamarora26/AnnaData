# Deploying AnnaData to Cloud Run

Why move: on Render's free tier a service sleeps after 15 minutes idle and takes
about a minute to wake, and the whole workspace has 750 instance-hours a month.
Two services awake around the clock would need 1,460 hours, so keeping them warm
is not merely awkward - it is arithmetically impossible, and pinging hard enough
to try it exhausts the allowance and suspends everything until the next month.

Cloud Run scales to zero the same way, but starts a container in seconds rather
than a minute, so there is nothing to keep warm. **The keep-warm cron jobs stop
being necessary at all.**

---

## Before you start

You need a Google Cloud project with billing enabled. Billing being enabled is
not the same as being charged: Cloud Run's always-free allowance covers 2 million
requests, 360,000 GB-seconds and 180,000 vCPU-seconds a month, and this service
will not come close. Set a budget alert anyway - see the last section.

Install the CLI once:

    https://cloud.google.com/sdk/docs/install

Then authenticate and pick the project:

    gcloud auth login
    gcloud config set project YOUR_PROJECT_ID
    gcloud services enable run.googleapis.com cloudbuild.googleapis.com

`asia-south1` (Mumbai) is the right region - it is closest to the farmers and to
the gateway handset.

---

## 1. Deploy the backend

From `AnnaData-Backend-main/`. There is no local Docker step: Cloud Build builds
the image from the Dockerfile in this directory.

    gcloud run deploy annadata-backend \
      --source . \
      --region asia-south1 \
      --allow-unauthenticated \
      --memory 1Gi \
      --cpu 1 \
      --min-instances 0 \
      --max-instances 3 \
      --timeout 300 \
      --set-env-vars "GOOGLE_API_KEY=...,DATABASE_URL=...,GOV_API_KEY=...,METNO_USER_AGENT=..."

`--min-instances 0` is what keeps this free; the service costs nothing while
idle. `--max-instances 3` is a spending guard rail, not a capacity plan.

For the Earth Engine service-account key, which is a whole JSON document and
awkward on a command line, set it in the console afterwards, or use a file:

    gcloud run services update annadata-backend --region asia-south1 \
      --set-env-vars "EE_SERVICE_KEY=$(cat path/to/key.json | tr -d '\n')"

Note the URL it prints. It looks like
`https://annadata-backend-xxxxx-el.a.run.app`.

## 2. Deploy the SMS bridge

From `AnnaData-SMS-main/`, using the backend URL from step 1:

    gcloud run deploy annadata-sms \
      --source . \
      --region asia-south1 \
      --allow-unauthenticated \
      --memory 512Mi \
      --cpu 1 \
      --min-instances 0 \
      --max-instances 3 \
      --timeout 300 \
      --set-env-vars "APP_USERNAME=...,PASSWORD=...,AI_ENDPOINT=https://BACKEND_URL/agent,SMS_MODE=cloud"

Add the WhatsApp variables here too if that channel is in use:
`WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_VERIFY_TOKEN`.

## 3. Check both before switching anything over

    curl -s https://BACKEND_URL/health
    curl -s https://BRIDGE_URL/health

The backend should report every feature true and `database: connected`. The
bridge should report `status: ok` and `backend_reachable: true`. If the bridge
says `degraded`, read `problems` - it names the failure rather than hiding it.

Then measure a cold start, which is the entire reason for the move: leave it
fifteen minutes, then

    curl -s -o /dev/null -w "%{time_total}s\n" https://BACKEND_URL/health

## 4. Point the gateway at the new bridge

Only after step 3 passes. The webhook is registered per account rather than per
device, so there is one URL to change and any handset on the account is covered.

The API has no PATCH for webhooks - it answers 404. Delete and recreate instead,
in that order: registering the new one first would leave both live for a moment,
and a farmer would get two replies to one question.

    curl -X DELETE https://api.sms-gate.app/3rdparty/v1/webhooks/OLD_ID \
      -u 'APP_USERNAME:PASSWORD'

    curl -X POST https://api.sms-gate.app/3rdparty/v1/webhooks \
      -u 'APP_USERNAME:PASSWORD' \
      -H 'Content-Type: application/json' \
      -d '{"url":"https://BRIDGE_URL/incoming-sms","event":"sms:received"}'

Keep the old URL to hand: if the create fails, re-register it immediately rather
than leaving the gateway with nowhere to deliver.

Send one real SMS and confirm a reply arrives.

## 5. Only now, retire the old setup

In this order, and not before a real message has been answered end to end:

1. Delete the cron-job.org keep-warm jobs. Cloud Run does not need them.
2. Keep the feedback job, repointed at `https://BRIDGE_URL/tasks/feedback` -
   that one does actual work rather than keeping anything warm. Once a day is
   plenty.
3. Leave the Render services in place for a few days as a fallback, then delete
   them.

---

## Two things that will bite on a fresh project

**The build fails before it starts.** A newly created project does not grant its
default compute service account the roles Cloud Build needs, so the first deploy
ends in `PERMISSION_DENIED ... could not resolve source`. Grant them once:

    SA=PROJECT_NUMBER-compute@developer.gserviceaccount.com
    for role in cloudbuild.builds.builder storage.objectViewer \
                artifactregistry.writer logging.logWriter; do
      gcloud projects add-iam-policy-binding PROJECT_ID \
        --member="serviceAccount:$SA" --role="roles/$role"
    done

**Do not deploy into the project that owns the Gemini API key.** Cloud Run
requires billing; a Gemini project with billing attached leaves the free tier and
starts answering `429 Your prepayment credits are depleted` - everywhere at once,
including any other deployment using that key. The two requirements are opposite,
so they need separate projects: billing on the one running Cloud Run, none on the
one that owns the key. Unlinking restores the free tier immediately.

---

## Cost, honestly

The always-free allowance is generous relative to this workload, and idle costs
nothing at `--min-instances 0`. The realistic ways to get a bill are a
misconfiguration that pins instances warm, or a burst of traffic, so:

    Billing -> Budgets & alerts -> create a budget of about 500 rupees
    with alerts at 50%, 90% and 100%

`--max-instances 3` caps the blast radius of anything unexpected.

## What changes in the code

Nothing, and that is the point. Both services already read `PORT` from the
environment and bind `0.0.0.0`, which is exactly what Cloud Run requires. The
Dockerfiles install `requirements.txt` only - PDF parsing, YAML and the AWS SDK
moved to `requirements-tools.txt`, because carrying them into every cold start
to support code the serving path never reaches is the wrong trade.
