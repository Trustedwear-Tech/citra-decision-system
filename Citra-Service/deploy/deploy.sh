#!/bin/bash
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

# Incremental deployment script for Citra AI Service
# Runs on Ubuntu, supports prod, test, or both environments

set -euo pipefail  # Exit on error, undefined var, pipe fail

echo "HOSTNAME=$HOSTNAME"

# Always run from script directory
cd "$(dirname "$0")"

# Global service arrays
prod_services=(
    "citra-ai-prod-1"
    "citra-ai-prod-2"
    "citra-ai-prod-3"
    "citra-ai-prod-4"
    "citra-ai-prod-5"
    "citra-ai-prod-6"
    "citra-ai-prod-7"
    "citra-ai-prod-8"
)

test_services=(
    "citra-ai-test-1"
    "citra-ai-test-2"
)

# Function to check service health
check_service_health() {
    local service=$1

    # Skip background / non-HTTP services
    if [[ "$service" == *"credit-flush"* ]] || [[ "$service" == *"sync-reconciliation"* ]]; then
        echo "$service is background service - skipping health check."
        return 0
    fi

    echo "Waiting for $service to be healthy..."
    for j in {1..300}; do
        if docker inspect "$service" --format '{{.State.Health.Status}}' 2>/dev/null | grep -q "healthy"; then
            echo "$service is healthy."
            return 0
        fi
        sleep 1
    done

    echo "ERROR: $service failed health check."
    return 1
}

# Generic deploy function
deploy_services() {
    local profile=$1
    local services_var=${profile}_services[@]
    local services=("${!services_var}")

    echo "Pulling new image for $profile..."
    docker compose --env-file .env."$profile" --profile "$profile" pull

    echo "Starting rolling deployment..."

    for service in "${services[@]}"; do
        echo "Restarting $service..."
        docker compose --env-file .env."$profile" --profile "$profile" up -d --no-deps --force-recreate "$service"

        if ! check_service_health "$service"; then
            echo "Deployment failed for $service"
            return 1
        fi
    done

    echo "$profile deployment completed."
}

# Rollback function
rollback() {
    local profile=$1
    local old_tag_var=old_${profile}_tag
    local old_tag=${!old_tag_var}
    echo "Rolling back $profile environment..."
    sed -i "s|^IMAGE_TAG=.*|IMAGE_TAG=$old_tag|g" .env.$profile
    docker compose --env-file .env.$profile --profile $profile pull

    local services_var=${profile}_services[@]
    for service in "${!services_var}"; do
        docker compose --env-file .env.$profile --profile $profile up -d --no-deps --force-recreate "$service"
    done
    echo "$profile rollback completed."
}

# Main script
echo "Citra AI Service Deployment Script"
echo "Select option: prod, test, both (default deploy), or log (view logs)"
read -r env
echo "Input new image tag (leave blank to keep current):"
read -r new_tag

if [ -z "$env" ]; then
    env="both"
fi

success_prod=false
success_test=false

case "$env" in
    prod)
        if [ -n "$new_tag" ]; then
            old_prod_tag=$(grep '^IMAGE_TAG=' .env.prod | cut -d'=' -f2)
            echo "Updating image tag to $new_tag for prod..."
            sed -i "s|^IMAGE_TAG=.*|IMAGE_TAG=$new_tag|g" .env.prod
            echo "Deploying prod with tag $new_tag"
        else
            current_prod_tag=$(grep '^IMAGE_TAG=' .env.prod | cut -d'=' -f2)
            echo "Deploying prod with existing tag: $current_prod_tag"
        fi
        if deploy_services "prod"; then
            success_prod=true
        else
            rollback "prod"
            exit 1
        fi
        ;;
    test)
        if [ -n "$new_tag" ]; then
            old_test_tag=$(grep '^IMAGE_TAG=' .env.test | cut -d'=' -f2)
            echo "Updating image tag to $new_tag for test..."
            sed -i "s|^IMAGE_TAG=.*|IMAGE_TAG=$new_tag|g" .env.test
            echo "Deploying test with tag $new_tag"
        else
            current_test_tag=$(grep '^IMAGE_TAG=' .env.test | cut -d'=' -f2)
            echo "Deploying test with existing tag: $current_test_tag"
        fi
        if deploy_services "test"; then
            success_test=true
        else
            rollback "test"
            exit 1
        fi
        ;;
    both)
        if [ -n "$new_tag" ]; then
            old_prod_tag=$(grep '^IMAGE_TAG=' .env.prod | cut -d'=' -f2)
            old_test_tag=$(grep '^IMAGE_TAG=' .env.test | cut -d'=' -f2)
            echo "Updating image tags to $new_tag for both prod and test..."
            sed -i "s|^IMAGE_TAG=.*|IMAGE_TAG=$new_tag|g" .env.prod
            sed -i "s|^IMAGE_TAG=.*|IMAGE_TAG=$new_tag|g" .env.test
            echo "Deploying both with tag $new_tag"
        else
            current_prod_tag=$(grep '^IMAGE_TAG=' .env.prod | cut -d'=' -f2)
            current_test_tag=$(grep '^IMAGE_TAG=' .env.test | cut -d'=' -f2)
            echo "Deploying prod with existing tag: $current_prod_tag"
            echo "Deploying test with existing tag: $current_test_tag"
        fi
        if deploy_services "prod"; then
            success_prod=true
            if deploy_services "test"; then
                success_test=true
            else
                rollback "test"
                exit 1
            fi
        else
            rollback "prod"
            exit 1
        fi
        ;;
    log)
        echo "Tailing logs for both prod and test environments. Press Ctrl+C to stop."
        docker compose --profile prod logs -f &
        PROD_LOG_PID=$!
        docker compose --profile test logs -f &
        TEST_LOG_PID=$!
        wait $PROD_LOG_PID $TEST_LOG_PID
        ;;
    *)
        echo "Invalid option. Please choose 'prod', 'test', 'both', or 'log'."
        exit 1
        ;;
esac

echo "Deployment script finished."

# Tail logs for the deployed environment(s) (only for deploy options)
if [ "$env" != "log" ]; then
    echo "Tailing logs for deployed environment(s). Press Ctrl+C to stop."
    if [ "$env" = "prod" ] || [ "$env" = "both" ]; then
        docker compose --profile prod logs -f &
        PROD_LOG_PID=$!
    fi
    if [ "$env" = "test" ] || [ "$env" = "both" ]; then
        docker compose --profile test logs -f &
        TEST_LOG_PID=$!
    fi

    # Wait for log processes (they run forever until interrupted)
    if [ -n "$PROD_LOG_PID" ] && [ -n "$TEST_LOG_PID" ]; then
        wait $PROD_LOG_PID $TEST_LOG_PID
    elif [ -n "$PROD_LOG_PID" ]; then
        wait $PROD_LOG_PID
    elif [ -n "$TEST_LOG_PID" ]; then
        wait $TEST_LOG_PID
    fi
fi