# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Authorization Middleware for Citra AI Service

This middleware runs AFTER JWTAuthMiddleware and checks resource-level permissions.
It intercepts requests to protected endpoints and validates that the authenticated
user has the required permission level to access the requested resource.

Flow:
1. JWTAuthMiddleware validates token and sets request.state.authenticated_user_id
2. AuthorizationMiddleware extracts resource_id from URL/body
3. AuthorizationMiddleware checks permission via AuthorizationService
4. If allowed, request proceeds to endpoint
5. If denied, returns 403 Forbidden
"""

import logging
import re
import json
from typing import Optional, Dict, Any, List, Tuple
from fastapi import HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


class AuthorizationMiddleware(BaseHTTPMiddleware):
    """
    Middleware that checks resource-level permissions after JWT authentication.
    
    This middleware:
    1. Identifies protected routes that require resource authorization
    2. Extracts resource_id and resource_type from the request
    3. Validates user has required permission level
    4. Blocks unauthorized access with 403 response
    """
    
    def __init__(self, app, dispatch=None):
        super().__init__(app, dispatch)
        
        # Protected route patterns with their resource type and required permission
        # Format: (pattern_regex, resource_type, required_permission, id_param_name)
        self.protected_routes: List[Tuple[str, str, str, str]] = [
            # Presentations
            (r"^/citra-ai/presentation/load/([^/]+)$", "presentation", "read", "presentation_id"),
            (r"^/presentation/load/([^/]+)$", "presentation", "read", "presentation_id"),
            (r"^/citra-ai/presentation/([^/]+)$", "presentation", "admin", "presentation_id"),  # DELETE
            (r"^/presentation/([^/]+)$", "presentation", "admin", "presentation_id"),  # DELETE
            
            # Reports (Composer)
            (r"^/citra-ai/composer/reports/([^/]+)$", "report", "read", "report_id"),
            (r"^/composer/reports/([^/]+)$", "report", "read", "report_id"),
            
            # Diagrams
            (r"^/citra-ai/diagram/get/([^/]+)$", "diagram", "read", "diagram_id"),
            (r"^/diagram/get/([^/]+)$", "diagram", "read", "diagram_id"),
            (r"^/citra-ai/diagram/delete/([^/]+)$", "diagram", "admin", "diagram_id"),
            (r"^/diagram/delete/([^/]+)$", "diagram", "admin", "diagram_id"),
            
            # Vaults (Folders)
            (r"^/citra-ai/folders/([^/]+)/documents$", "vault", "read", "folder_id"),
            (r"^/folders/([^/]+)/documents$", "vault", "read", "folder_id"),
            (r"^/citra-ai/api/vault-sharing/check-access/([^/]+)$", "vault", "read", "vault_id"),
            (r"^/api/vault-sharing/check-access/([^/]+)$", "vault", "read", "vault_id"),
            
            # Chat Sessions
            (r"^/citra-ai/chat/session/([^/]+)$", "chat", "read", "session_id"),
            (r"^/chat/session/([^/]+)$", "chat", "read", "session_id"),
            
            # Projects
            (r"^/citra-ai/api/projects/([^/]+)$", "project", "read", "project_id"),
            (r"^/api/projects/([^/]+)$", "project", "read", "project_id"),
        ]
        
        # Routes that skip authorization (list endpoints, public endpoints)
        self.skip_routes: List[str] = [
            # List endpoints (handled separately with user filtering)
            r"^/citra-ai/presentation/list",
            r"^/presentation/list",
            r"^/citra-ai/composer/reports$",
            r"^/composer/reports$",
            r"^/citra-ai/diagram/list",
            r"^/diagram/list",
            
            # Public share endpoints
            r"^/citra-ai/api/public-share/public/",
            r"^/api/public-share/public/",
            r"^/public/",
            
            # Health and docs
            r"^/health",
            r"^/docs",
            r"^/openapi.json",
            r"^/redoc",
            
            # Sharing API (has its own authorization logic)
            r"^/citra-ai/api/sharing/",
            r"^/api/sharing/",
            
            # Auth endpoints
            r"^/citra-ai/auth/",
            r"^/auth/",
        ]
        
        # Compile regex patterns for performance
        self.protected_patterns = [
            (re.compile(pattern), resource_type, permission, id_param)
            for pattern, resource_type, permission, id_param in self.protected_routes
        ]
        
        self.skip_patterns = [re.compile(pattern) for pattern in self.skip_routes]
        
        logger.info("✅ AuthorizationMiddleware initialized")
    
    def _should_skip(self, path: str) -> bool:
        """Check if this path should skip authorization."""
        for pattern in self.skip_patterns:
            if pattern.match(path):
                return True
        return False
    
    def _match_protected_route(self, path: str, method: str) -> Optional[Tuple[str, str, str, str]]:
        """
        Match path against protected routes.
        
        Returns:
            Tuple of (resource_id, resource_type, required_permission, param_name) or None
        """
        for pattern, resource_type, permission, param_name in self.protected_patterns:
            match = pattern.match(path)
            if match:
                resource_id = match.group(1)
                
                # Adjust permission based on HTTP method
                if method == "DELETE":
                    permission = "admin"
                elif method in ("PUT", "PATCH", "POST"):
                    permission = "write"
                
                return (resource_id, resource_type, permission, param_name)
        
        return None
    
    async def _extract_resource_id_from_body(self, request: Request) -> Optional[Dict[str, str]]:
        """
        Extract resource_id from request body for POST/PUT requests.
        
        Returns:
            Dict with resource_id and resource_type if found
        """
        try:
            content_type = request.headers.get("content-type", "")
            
            if not content_type.startswith("application/json"):
                return None
            
            body = await request.body()
            if not body:
                return None
            
            # Reset request body for subsequent reads
            async def receive():
                return {"type": "http.request", "body": body}
            request._receive = receive
            
            data = json.loads(body.decode())
            
            # Map of body fields to resource types
            field_mappings = {
                "presentation_id": "presentation",
                "report_id": "report",
                "diagram_id": "diagram",
                "folder_id": "vault",
                "vault_id": "vault",
                "session_id": "chat",
                "project_id": "project",
                "document_id": "document"
            }
            
            for field, resource_type in field_mappings.items():
                if field in data and data[field]:
                    return {
                        "resource_id": data[field],
                        "resource_type": resource_type
                    }
            
            return None
            
        except Exception as e:
            logger.debug(f"Could not extract resource_id from body: {e}")
            return None
    
    async def dispatch(self, request: Request, call_next):
        """
        Process the request and check authorization.
        """
        path = request.url.path
        method = request.method
        
        # Skip authorization for certain routes
        if self._should_skip(path):
            return await call_next(request)
        
        # Skip OPTIONS (CORS preflight)
        if method == "OPTIONS":
            return await call_next(request)
        
        # Check if this is a protected route
        match_result = self._match_protected_route(path, method)
        
        if not match_result:
            # Not a protected route, proceed
            return await call_next(request)
        
        resource_id, resource_type, required_permission, param_name = match_result
        
        # Get authenticated user_id from request state (set by JWTAuthMiddleware)
        user_id = getattr(request.state, 'authenticated_user_id', None)
        
        if not user_id:
            logger.warning(f"🔒 Authorization check skipped: No authenticated user")
            # Let the request proceed - the endpoint should handle auth
            return await call_next(request)
        
        # Check authorization
        try:
            from services.authorization_service import get_authorization_service
            
            auth_service = get_authorization_service()
            access_result = await auth_service.check_access(
                user_id=user_id,
                resource_id=resource_id,
                resource_type=resource_type,
                required_permission=required_permission
            )
            
            if access_result.get("allowed"):
                # Store permission info in request state for endpoint use
                request.state.resource_permission = access_result.get("permission")
                request.state.is_resource_owner = access_result.get("is_owner", False)
                
                logger.debug(f"✅ Authorization granted: {user_id} -> {resource_type}:{resource_id}")
                return await call_next(request)
            else:
                # Access denied
                reason = access_result.get("reason", "Access denied")
                logger.warning(f"🔒 Authorization denied: {user_id} -> {resource_type}:{resource_id} - {reason}")
                
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={
                        "detail": "Access denied. You don't have permission to access this resource.",
                        "error": "authorization_denied",
                        "resource_type": resource_type,
                        "required_permission": required_permission
                    }
                )
                
        except Exception as e:
            logger.error(f"❌ Authorization check failed: {e}", exc_info=True)
            # On error, let the request proceed and let endpoint handle it
            # This prevents authorization errors from blocking all requests
            return await call_next(request)


# =========================================================================
# FastAPI Dependency for Manual Authorization Checks
# =========================================================================

async def require_resource_access(
    request: Request,
    resource_id: str,
    resource_type: str,
    required_permission: str = "read"
) -> Dict[str, Any]:
    """
    FastAPI dependency for manual authorization checks in endpoints.
    
    Usage:
        @router.get("/resource/{id}")
        async def get_resource(
            id: str,
            request: Request,
            auth: dict = Depends(lambda r: require_resource_access(r, id, "presentation"))
        ):
            ...
    
    Args:
        request: FastAPI Request object
        resource_id: The resource being accessed
        resource_type: Type of resource
        required_permission: Minimum permission required
        
    Returns:
        Dict with user_id, permission, and is_owner
        
    Raises:
        HTTPException 403 if access denied
    """
    from citra_auth import get_secure_user_id
    from services.authorization_service import get_authorization_service
    
    user_id = get_secure_user_id(request)
    auth_service = get_authorization_service()
    
    access_result = await auth_service.check_access(
        user_id=user_id,
        resource_id=resource_id,
        resource_type=resource_type,
        required_permission=required_permission
    )
    
    if not access_result.get("allowed"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "authorization_denied",
                "message": access_result.get("reason", "Access denied"),
                "resource_type": resource_type,
                "required_permission": required_permission
            }
        )
    
    return {
        "user_id": user_id,
        "permission": access_result.get("permission"),
        "is_owner": access_result.get("is_owner", False)
    }


def create_authorization_dependency(resource_type: str, permission: str = "read"):
    """
    Factory function to create authorization dependencies.
    
    Usage:
        require_presentation_read = create_authorization_dependency("presentation", "read")
        
        @router.get("/presentation/{id}")
        async def get_presentation(
            id: str,
            request: Request,
            auth: dict = Depends(require_presentation_read(id))
        ):
            ...
    """
    def dependency_factory(resource_id: str):
        async def dependency(request: Request):
            return await require_resource_access(
                request, resource_id, resource_type, permission
            )
        return dependency
    return dependency_factory
