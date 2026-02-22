#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

if [ $# -ne 1 ]; then
  echo "Usage: sh submit.sh [dev|prod]" >&2
  exit 1
fi



ENV_NAME="$1"
ENV_FILE="$SCRIPT_DIR/env.$ENV_NAME"

if [ ! -f "$ENV_FILE" ]; then
  echo "Environment file $ENV_FILE not found!" >&2
  exit 1
fi

# shellcheck disable=SC1090
. "$ENV_FILE"


SERVICE_ACCOUNT_RESOURCE="projects/${PROJECT_ID}/serviceAccounts/${SERVICE_ACCOUNT_EMAIL}"

ARTIFACT_REPO="webapp"
IMAGE_NAME="webapp"
IMAGE_TAG="latest"
IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPO}/${IMAGE_NAME}:${IMAGE_TAG}"

CACHE_IMAGE_NAME="cache"
CACHE_IMAGE_TAG="latest"
CACHE_REPO="${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPO}/${CACHE_IMAGE_NAME}"
CACHE_IMAGE_URI="${CACHE_REPO}:${CACHE_IMAGE_TAG}"
REBUILD_CACHE_IMAGE="${REBUILD_CACHE_IMAGE:-0}"

DEPLOY_NAME="the-list"

export GOOGLE_APPLICATION_CREDENTIALS="$SCRIPT_DIR/$SERVICE_ACCOUNT_KEY_FILE"
gcloud auth activate-service-account "$SERVICE_ACCOUNT_EMAIL" --key-file "$GOOGLE_APPLICATION_CREDENTIALS" >/dev/null
gcloud config set account "$SERVICE_ACCOUNT_EMAIL" >/dev/null
gcloud config set project "$PROJECT_ID" >/dev/null

echo "Using PROJECT_ID: $PROJECT_ID"
echo "Using REGION: $REGION"
echo "Using IMAGE_URI: $IMAGE_URI"
echo "Using CACHE_IMAGE_URI: $CACHE_IMAGE_URI"

check_cache_image() {
  echo "Checking cache image: $CACHE_IMAGE_URI"
  if gcloud artifacts docker images describe "$CACHE_IMAGE_URI" --project="$PROJECT_ID" >/dev/null 2>&1; then
    echo "Cache image exists"
    return 0
  fi
  echo "Cache image not found"
  return 1
}

build_cache_image() {
  echo "Building cache image with scripts/cloudbuild.webapp-cache.yaml"
  gcloud builds submit "$SCRIPT_DIR" \
    --config="$SCRIPT_DIR/scripts/cloudbuild.webapp-cache.yaml" \
    --region="$REGION" \
    --project="$PROJECT_ID" \
    --service-account="$SERVICE_ACCOUNT_RESOURCE" \
    --substitutions="_CACHE_IMAGE_URI=${CACHE_IMAGE_URI},_CACHE_REPO=${CACHE_REPO}"
}

if [ "$REBUILD_CACHE_IMAGE" != "0" ]; then
  echo "Forcing cache rebuild"
  build_cache_image
elif ! check_cache_image; then
  echo "Cache missing, building"
  build_cache_image
else
  echo "Using existing cache image"
fi

echo "Submitting build with scripts/cloudbuild.webapp.yaml"
gcloud builds submit "$SCRIPT_DIR" \
  --config="$SCRIPT_DIR/scripts/cloudbuild.webapp.yaml" \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --service-account="$SERVICE_ACCOUNT_RESOURCE" \
  --substitutions="_IMAGE_URI=${IMAGE_URI},_CACHE_IMAGE_URI=${CACHE_IMAGE_URI},_CACHE_REPO=${CACHE_REPO}"


echo "Deploying $DEPLOY_NAME to Cloud Run"
gcloud run deploy "$DEPLOY_NAME" \
  --image "$IMAGE_URI" \
  --platform managed \
  --region "$REGION" \
  --allow-unauthenticated \
  --port 8087 \
  --cpu 2 \
  --memory 4Gi \
  --timeout 3600 \
  --args="--env=$ENV_NAME" \
  --set-secrets="SERVICE_ACCOUNT_KEY=service_account_key:latest","ENV_FILE=env_file:latest" \
  --service-account=$SERVICE_ACCOUNT_EMAIL
