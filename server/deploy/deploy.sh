#!/usr/bin/env bash
# Run on the VPS: bash deploy.sh <40-character SHA> <release directory>.
# Only application containers/images/configuration are replaced; data is never restored/deleted.
set -Eeuo pipefail
umask 077

version=${1:?Expected a commit SHA}
release_input=${2:?Expected a release directory}
[[ "$version" =~ ^[0-9a-f]{40}$ ]] || { echo 'Invalid commit SHA' >&2; exit 2; }
app_root=$(realpath -m "${APP_ROOT:-/home/kpsztest/client-server-test}")
release=$(realpath -e "$release_input")
[[ "$release" == "$app_root/releases/$version" ]] || {
  echo 'Release must be inside the application releases directory' >&2
  exit 2
}
repository_url=https://github.com/test-max607/client-server-test.git
image="client-server-test:$version"
mkdir -p "$app_root/data"
exec 9>"$app_root/.deploy.lock"
flock -w 300 9
cd "$release"

active_env="$app_root/active.env"
active_compose="$app_root/active.compose.yaml"
current_file="$app_root/current.version"

compose() {
  docker compose --project-name client-server-test --project-directory "$app_root" \
    --env-file "$active_env" -f "$active_compose" "$@"
}

probe() {
  compose exec -T server python -m server.deploy.smoke --expected-version "$1"
}

# Called by CI if its external REST/gRPC probe fails after a locally healthy deployment.
# Refuse to undo a newer release that another deployment might already have installed.
if [[ "${3:-}" == --rollback ]]; then
  [[ -f "$current_file" && "$(cat "$current_file")" == "$version" ]] || {
    echo 'Current version changed; refusing to roll back another release' >&2
    exit 1
  }
  if [[ -f "$release/prior.version" ]]; then
    cp -- "$release/prior.env" "$active_env"
    cp -- "$release/prior.compose.yaml" "$active_compose"
    cp -- "$release/prior.version" "$current_file"
    compose up --detach --wait --wait-timeout 90
    probe "$(cat "$current_file")"
  else
    compose down --timeout 15
    rm -f -- "$active_env" "$active_compose" "$current_file"
  fi
  echo "Rolled back externally failed release $version; database preserved."
  exit 0
fi

# A new deployment requires an intact archive. Rollback uses the already-loaded
# previous image and saved configuration, even after an archive has been removed.
sha256sum --strict --check SHA256SUMS

latest_version() {
  timeout 30 git ls-remote "$repository_url" refs/heads/main | awk '{print $1}'
}

latest=$(latest_version)
[[ "$latest" =~ ^[0-9a-f]{40}$ ]] || { echo 'Could not resolve main revision' >&2; exit 1; }
if [[ "$latest" != "$version" ]]; then
  echo "Skipping stale release $version; main has advanced."
  exit 0
fi

docker info >/dev/null
docker compose version
docker load --input "$release/image.tar.gz"
loaded_version=$(docker image inspect "$image" --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}')
[[ "$loaded_version" == "$version" ]] || { echo 'Image version mismatch' >&2; exit 1; }

previous_version=''
if [[ -e "$active_env" || -e "$active_compose" || -e "$current_file" ]]; then
  [[ -f "$active_env" && -f "$active_compose" && -f "$current_file" ]] || {
    echo 'Incomplete active deployment state; refusing to overwrite it' >&2
    exit 1
  }
  previous_version=$(cat "$current_file")
  [[ "$previous_version" =~ ^[0-9a-f]{40}$ ]] || exit 1
  cp -- "$active_env" "$release/prior.env"
  cp -- "$active_compose" "$release/prior.compose.yaml"
  cp -- "$current_file" "$release/prior.version"
fi

printf 'APP_IMAGE=%s\nAPP_VERSION=%s\nAPP_DATA_DIR=%s\nHTTP_PORT=8000\nGRPC_PORT=50051\n' \
  "$image" "$version" "$app_root/data" >"$release/staged.env"
docker compose --project-name client-server-test --project-directory "$app_root" \
  --env-file "$release/staged.env" -f "$release/compose.yaml" config --quiet

# Check again after the potentially slow image load, immediately before replacing the service.
latest=$(latest_version)
[[ "$latest" =~ ^[0-9a-f]{40}$ ]] || { echo 'Could not resolve main revision' >&2; exit 1; }
if [[ "$latest" != "$version" ]]; then
  echo "Skipping stale release $version before service replacement."
  exit 0
fi

rollback() {
  local failure_status=$?
  trap - ERR INT TERM
  set +e
  echo "Deployment failed; restoring the previous application (database is unchanged)." >&2
  compose logs --tail 80 server >&2
  if [[ -n "$previous_version" ]]; then
    cp -- "$release/prior.env" "$active_env"
    cp -- "$release/prior.compose.yaml" "$active_compose"
    cp -- "$release/prior.version" "$current_file"
    if compose up --detach --wait --wait-timeout 90 && probe "$previous_version"; then
      echo "Restored $previous_version" >&2
    else
      echo 'Rollback could not start the previous version; administrator attention is required.' >&2
    fi
  else
    compose down --timeout 15
    rm -f -- "$active_env" "$active_compose" "$current_file"
    echo 'No previous version exists; the failed first deployment was stopped.' >&2
  fi
  if [[ "$failure_status" == 0 ]]; then failure_status=1; fi
  exit "$failure_status"
}

trap rollback ERR
# A signal should also restore the previous version once replacement has begun.
trap 'rollback' INT TERM
cp -- "$release/staged.env" "$active_env"
cp -- "$release/compose.yaml" "$active_compose"
compose up --detach --wait --wait-timeout 90
probe "$version"
printf '%s\n' "$version" >"$current_file"
if [[ -n "$previous_version" && "$previous_version" != "$version" ]]; then
  cp -- "$release/prior.env" "$app_root/previous.env"
  cp -- "$release/prior.compose.yaml" "$app_root/previous.compose.yaml"
  cp -- "$release/prior.version" "$app_root/previous.version"
fi
trap - ERR INT TERM
echo "Deployment ready: $version"

# Only prune this application's commit-tagged images, keeping current and previous versions.
retained_previous=''
if [[ -f "$app_root/previous.version" ]]; then retained_previous=$(cat "$app_root/previous.version"); fi
while IFS= read -r candidate; do
  candidate_version=${candidate#client-server-test:}
  if [[ "$candidate" =~ ^client-server-test:[0-9a-f]{40}$ ]] \
    && [[ "$candidate_version" != "$version" && "$candidate_version" != "$retained_previous" ]]; then
    docker image rm "$candidate" || echo "Could not remove unused image $candidate" >&2
  fi
done < <(docker image ls client-server-test --format '{{.Repository}}:{{.Tag}}')
# Retain only current/previous archives for diagnosis and an explicit redeployment.
while IFS= read -r -d '' archive; do
  archive_version=$(basename "$(dirname "$archive")")
  if [[ "$archive_version" != "$version" && "$archive_version" != "$retained_previous" ]]; then
    rm -f -- "$archive"
  fi
done < <(find "$app_root/releases" -mindepth 2 -maxdepth 2 -type f -name image.tar.gz -print0)
