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

set -e  # Exit on any error

# Function to check service health
check_service_health() {
    local service=$1
    local profile=$2

    if [[ "$service" == *"credit-flush"* ]]; then
        echo "$service is a background service - skipping health check."
        return 0
    fi

    echo "Waiting for $service to be healthy..."
    local healthy=false
    for j in {1..300}; do
        if docker inspect "$service" --format '{{.State.Health.Status}}' 2>/dev/null | grep -q "healthy"; then
            healthy=true
            echo "$service is healthy."
            break
        fi
        sleep 1
    done

    if [ "$healthy" = false ]; then
        echo "ERROR: $service failed to become healthy after 5 minutes. Aborting."
        exit 1
    fi
}

# Generic deploy function
deploy_services() {
    local profile=$1
    shift
    local services=("$@")

    echo "Starting incremental deployment for ${profile^^} environment..."

    for service in "${services[@]}"; do
        echo "Stopping $service..."
        docker compose --profile "$profile" stop "$service" || true  # Ignore if not running

        echo "Rebuilding and starting $service..."
        docker compose --profile "$profile" up -d --build --no-deps "$service"

        check_service_health "$service" "$profile"
    done

    echo "${profile^^} deployment completed."
}

# Function to deploy prod environment
deploy_prod() {
    local services=(
        "citra-ai-prod-1"
        "citra-ai-prod-2"
        "citra-ai-prod-3"
        "citra-ai-prod-4"
        "citra-ai-prod-5"
        "citra-ai-prod-6"
        "citra-ai-prod-7"
        "citra-ai-prod-8"
        "citra-ai-prod-credit-flush"
    )
    deploy_services "prod" "${services[@]}"
}

# Function to deploy test environment
deploy_test() {
    local services=(
        "citra-ai-test-1"
        "citra-ai-test-credit-flush"
    )
    deploy_services "test" "${services[@]}"
}

# Main script
echo "Citra AI Service Deployment Script"
echo "Select option: prod, test, both (default deploy), or log (view logs)"
read -r env

if [ -z "$env" ]; then
    env="both"
fi

case "$env" in
    prod)
        deploy_prod
        ;;
    test)
        deploy_test
        ;;
    both)
        deploy_prod
        deploy_test
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