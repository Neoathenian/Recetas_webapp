#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

. "$SCRIPT_DIR/env.dev"


SERVICE_ACCOUNT_RESOURCE="projects/${PROJECT_ID}/serviceAccounts/${SERVICE_ACCOUNT_EMAIL}"


export GOOGLE_APPLICATION_CREDENTIALS="$SCRIPT_DIR/$SERVICE_ACCOUNT_KEY_FILE"
gcloud auth activate-service-account "$SERVICE_ACCOUNT_EMAIL" --key-file "$GOOGLE_APPLICATION_CREDENTIALS" >/dev/null
gcloud config set account "$SERVICE_ACCOUNT_EMAIL" >/dev/null
gcloud config set project "$PROJECT_ID" >/dev/null

ARTIFACT_REPO="webapp"
CACHE_IMAGE_NAME="cache"
CACHE_IMAGE_TAG="latest"
CACHE_REPO="${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPO}/${CACHE_IMAGE_NAME}"
CACHE_IMAGE_URI="${CACHE_REPO}:${CACHE_IMAGE_TAG}"


gcloud auth activate-service-account "$SERVICE_ACCOUNT_EMAIL" --key-file "$GOOGLE_APPLICATION_CREDENTIALS" >/dev/null
gcloud builds submit "$SCRIPT_DIR" \
  --config="$SCRIPT_DIR/scripts/cloudbuild.webapp-cache.yaml" \
  --region="$REGION" \
  --service-account="projects/the-list-webapp-dev/serviceAccounts/deployment-the-list-sa-dev@the-list-webapp-dev.iam.gserviceaccount.com" \
  --substitutions="_CACHE_IMAGE_URI=${CACHE_IMAGE_URI},_CACHE_REPO=${CACHE_REPO}"
