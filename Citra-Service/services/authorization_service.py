# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Centralized Authorization Service for Citra AI

This service handles all resource access control across the platform:
- Vaults (folders)
- Presentations
- Reports
- Diagrams
- Chat sessions
- Documents

Key Features:
- Unified permission model for all resource types
- Owner and shared access validation
- Workspace-based access control
- Automatic revocation on workspace member removal
- Audit trail for all permission changes
"""

import logging
from typing import List, Dict, Any, Optional, Literal
from datetime import datetime
from bson import ObjectId
from bson.errors import InvalidId
from motor.motor_asyncio import AsyncIOMotorClient

from citra_mongo import get_mongo_client, MONGODB_DATABASE

logger = logging.getLogger(__name__)

# Resource types supported by the authorization system
# Using a frozenset for O(1) membership checks and immutability
RESOURCE_TYPES = frozenset({
    # Personal resources (admin-invisible, hard-deleted on user removal)
    "vault",           # Folders (collection: folders)
    "presentation",    # Presentations
    "report",          # Composer Reports (collection: composer_reports)
    "diagram",         # Diagrams (Mermaid/Cytoscape) — mindmaps share this collection
    "chat",            # Chat sessions
    "document",        # Documents in vaults
    "project",         # Projects/Cases
    "printable",       # Printable documents
    "template",        # Templates
    "note",            # Notes (collection: Notes)
    "page",            # Page-builder pages
    # Managed resources (admin-visible, transferable on user removal)
    "workflow",        # Citra workflow definitions
    "smart_app",       # Smart Apps (AppSpec)
})

# Resource classes (consumed by admin page filters + user-delete worker).
# Source of truth: see config.resource_taxonomy (Python) +
# services.resourceTaxonomy (JS) — keep these three lists in lockstep.
MANAGED_RESOURCES = frozenset({"workflow", "smart_app"})
PERSONAL_RESOURCES = RESOURCE_TYPES - MANAGED_RESOURCES

# Permission levels (higher number = more permissions)
PERMISSION_LEVELS = {
    "read": 1,      # Can view
    "write": 2,     # Can view and edit
    "admin": 3      # Can view, edit, delete, and manage sharing
}

# Default permission for sharing
DEFAULT_PERMISSION = "read"


class AuthorizationService:
    """
    Centralized service for managing resource access permissions.
    
    This service provides:
    - Permission checking (can user access resource?)
    - Permission granting (share resource with user)
    - Permission revoking (remove user access)
    - Bulk revocation (when user leaves workspace)
    - Resource listing with permission filtering
    """
    
    def __init__(self, db=None):
        """Initialize the authorization service."""
        if db is None:
            client = get_mongo_client()
            self.db = client[MONGODB_DATABASE]
        else:
            self.db = db
        
        # Main permissions collection
        self.permissions_collection = self.db["resource_permissions"]
        
        # Team members collection (replacing legacy workspace_members)
        self.team_members = self.db["team_members"]
        
        # Keep legacy reference for backward compatibility if needed, but primary is team_members
        self.workspace_members = self.db["workspace_members"]
        
        # Audit log collection
        self.audit_log = self.db["authorization_audit_log"]
        
        logger.info("✅ AuthorizationService initialized")
    
    # =========================================================================
    # Permission Checking
    # =========================================================================
    
    async def check_access(
        self,
        user_id: str,
        resource_id: str,
        resource_type: str,
        required_permission: str = "read"
    ) -> Dict[str, Any]:
        """
        Check if a user has access to a specific resource.
        
        Args:
            user_id: The user requesting access
            resource_id: The resource being accessed
            resource_type: Type of resource (vault, presentation, etc.)
            required_permission: Minimum permission level required (read, write, admin)
            
        Returns:
            Dict with:
                - allowed: bool - whether access is granted
                - permission: str - the user's permission level (if allowed)
                - reason: str - explanation of the decision
                - is_owner: bool - whether user is the owner
        """
        try:
            logger.info(f"🔐 Checking access: user={user_id}, resource={resource_id}, type={resource_type}")
            
            # Get permission record for this resource
            permission_record = await self.permissions_collection.find_one({
                "resource_id": resource_id,
                "resource_type": resource_type
            })
            
            # Helper for readable code
            perm_record = permission_record
            
            # If no permission record exists, check legacy collections
            if not permission_record:
                # Try to find in legacy collection based on resource type
                legacy_result = await self._check_legacy_access(
                    user_id, resource_id, resource_type
                )
                if legacy_result:
                    return legacy_result
                
                logger.warning(f"⚠️ No permission record found for {resource_type}:{resource_id}")
                return {
                    "allowed": False,
                    "permission": None,
                    "reason": "Resource not found or no permissions defined",
                    "is_owner": False
                }
            
            # Check if user is the owner
            owner_id = permission_record.get("owner_id")
            if owner_id == user_id:
                logger.info(f"✅ Access granted: {user_id} is owner of {resource_type}:{resource_id}")
                return {
                    "allowed": True,
                    "permission": "admin",  # Owners have full admin access
                    "reason": "User is the resource owner",
                    "is_owner": True,
                    "owner_id": owner_id
                }
            
            # Check if user is in shared_with list
            shared_with = permission_record.get("shared_with", [])
            for share_entry in shared_with:
                if share_entry.get("user_id") == user_id:
                    user_permission = share_entry.get("permission", "read")
                    
                    # Check if permission meets required level
                    if PERMISSION_LEVELS.get(user_permission, 0) >= PERMISSION_LEVELS.get(required_permission, 1):
                        logger.info(f"✅ Access granted: {user_id} has {user_permission} access to {resource_type}:{resource_id}")
                        return {
                            "allowed": True,
                            "permission": user_permission,
                            "reason": f"User has shared {user_permission} access",
                            "is_owner": False,
                            "owner_id": owner_id,
                            "shared_by": share_entry.get("shared_by"),
                            "shared_at": share_entry.get("shared_at")
                        }
                    else:
                        logger.warning(f"⚠️ Insufficient permission: {user_id} has {user_permission}, needs {required_permission}")
                        return {
                            "allowed": False,
                            "permission": user_permission,
                            "reason": f"Insufficient permission: has {user_permission}, needs {required_permission}",
                            "is_owner": False
                        }

            
            # Check team-based access (New)
            if perm_record.get("shared_with_teams"):
                # Get user's teams
                user_teams = await self.team_members.find({
                    "user_id": user_id, 
                    "status": "active"
                }).to_list(length=100)
                
                user_team_ids = [str(t.get("team_id")) for t in user_teams]
                
                if user_team_ids:
                    shared_teams = perm_record.get("shared_with_teams", [])
                    for team_share in shared_teams:
                        if team_share.get("team_id") in user_team_ids:
                            team_permission = team_share.get("permission", "read")
                             
                            # Check if team permission is sufficient
                            if PERMISSION_LEVELS.get(team_permission, 0) >= PERMISSION_LEVELS.get(required_permission, 1):
                                logger.info(f"✅ Access granted: {user_id} inherits {team_permission} via Team {team_share.get('team_id')}")
                                return {
                                    "allowed": True,
                                    "permission": team_permission,
                                    "reason": f"User inherits {team_permission} access from Team",
                                    "is_owner": False,
                                    "owner_id": owner_id,
                                    "shared_via_team": team_share.get("team_id")
                                }
            
            # User not found in owner or shared_with or shared_with_teams
            logger.warning(f"🔒 Access denied: {user_id} has no access to {resource_type}:{resource_id}")
            return {
                "allowed": False,
                "permission": None,
                "reason": "User does not have access to this resource",
                "is_owner": False
            }
            
        except Exception as e:
            logger.error(f"❌ Error checking access: {e}", exc_info=True)
            return {
                "allowed": False,
                "permission": None,
                "reason": f"Authorization check failed: {str(e)}",
                "is_owner": False
            }
    
    async def _check_legacy_access(
        self,
        user_id: str,
        resource_id: str,
        resource_type: str
    ) -> Optional[Dict[str, Any]]:
        """
        Check access in legacy collections (for backward compatibility).
        
        This handles resources that haven't been migrated to the new
        centralized permission system yet.
        """
        try:
            # Map resource types to their legacy collections
            legacy_collections = {
                "presentation": "presentations",
                "report": "composer_reports",
                "diagram": "diagrams",
                "vault": "folders",
                "document": "document_chunked",
                "chat": "chat_sessions",
                "project": "projects",
                "printable": "printables",
            }
            
            collection_name = legacy_collections.get(resource_type)
            if not collection_name:
                return None
            
            collection = self.db[collection_name]
            
            # Try to find the resource
            # Handle both ObjectId and string IDs
            query_id = resource_id
            if resource_type in ["diagram", "report"] and len(resource_id) == 24:
                try:
                    query_id = ObjectId(resource_id)
                except (InvalidId, TypeError):
                    # Not a valid ObjectId → keep the raw string and let the
                    # string-_id lookup below handle it. Any OTHER error must
                    # propagate, not be swallowed here.
                    pass
            
            resource = await collection.find_one({"_id": query_id})
            
            if not resource:
                # Try with string _id
                resource = await collection.find_one({"_id": resource_id})
            
            if not resource:
                return None
            
            # Check if user is owner
            owner_id = resource.get("user_id")
            if owner_id == user_id:
                return {
                    "allowed": True,
                    "permission": "admin",
                    "reason": "User is the resource owner (legacy)",
                    "is_owner": True
                }
            
            # Check legacy shared_with field
            shared_with = resource.get("shared_with", [])
            if user_id in shared_with:
                return {
                    "allowed": True,
                    "permission": "read",
                    "reason": "User has shared access (legacy)",
                    "is_owner": False
                }
            
            # Check shared_with_me collection for this user
            shared_entry = await self.db["shared_with_me"].find_one({
                "user_id": user_id,
                "source_id": resource_id,
                "content_type": resource_type
            })
            
            if shared_entry:
                return {
                    "allowed": True,
                    "permission": shared_entry.get("permission", "read"),
                    "reason": "User has shared access (shared_with_me)",
                    "is_owner": False
                }
            
            # Check vault_shares collection for vault type
            if resource_type == "vault":
                # Need to get user's email to check vault_shares
                user_record = await self.db["users"].find_one({"_id": user_id})
                if not user_record:
                    user_record = await self.db["users"].find_one({"uid": user_id})
                
                if user_record:
                    user_email = user_record.get("email", "").lower()
                    if user_email:
                        vault_share = await self.db["vault_shares"].find_one({
                            "vault_id": resource_id,
                            "shared_with_email": user_email,
                            "status": "active"
                        })
                        
                        if vault_share:
                            return {
                                "allowed": True,
                                "permission": vault_share.get("permission", "read"),
                                "reason": "User has shared access (vault_shares)",
                                "is_owner": False
                            }
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Error checking legacy access: {e}")
            return None
    
    # =========================================================================
    # Permission Granting (Sharing)
    # =========================================================================
    
    async def grant_access(
        self,
        owner_id: str,
        resource_id: str,
        resource_type: str,
        target_id: str,  # Can be user_id or team_id
        target_type: str = "user", # "user" or "team"
        permission: str = "read",
        team_id: Optional[str] = None # Context workspace (legacy/optional)
    ) -> Dict[str, Any]:
        """
        Grant access to a resource for a user OR a team.
        
        Args:
            owner_id: User granting the access
            resource_id: Resource to share
            resource_type: Type of resource
            target_id: ID of the user or team to grant access to
            target_type: "user" or "team"
            permission: Permission level (read, write, admin)
            team_id: Optional workspace context
        """
        try:
            # Handle backward compatibility where target_id passed as target_user_id keyword
            # This is specific for calls that might still use keyword args matching the old signature
            # But here we updated signature to target_id.
            # (Assuming callers update or we wrap. For now, we update signature)
            
            logger.info(f"🔗 Granting {permission} access: {target_type}={target_id} -> {resource_type}:{resource_id}")
            
            # Validate permission level
            if permission not in PERMISSION_LEVELS:
                return {
                    "success": False,
                    "error": f"Invalid permission level: {permission}. Must be one of: {list(PERMISSION_LEVELS.keys())}"
                }
            
            # Get or create permission record
            permission_record = await self.permissions_collection.find_one({
                "resource_id": resource_id,
                "resource_type": resource_type
            })
            
            now = datetime.utcnow()
            
            if permission_record:
                # Verify the granting user has admin access
                record_owner = permission_record.get("owner_id")
                if record_owner != owner_id:
                    # Check if user has admin permission
                    access_check = await self.check_access(owner_id, resource_id, resource_type, "admin")
                    if not access_check.get("allowed"):
                        return {
                            "success": False,
                            "error": "Only the owner or admins can share this resource"
                        }
                
            if target_type == "user":
                # ... Existing User Logic ...
                shared_list_field = "shared_with"
                id_field = "user_id"
                
                # Check duplication logic
                shared_list = permission_record.get(shared_list_field, []) if permission_record else []
                # ... (rest of user logic adapted below)
            else: # target_type == "team"
                shared_list_field = "shared_with_teams"
                id_field = "team_id"
            
            now = datetime.utcnow()

            if permission_record:
                # Calculate existing index
                shared_list = permission_record.get(shared_list_field, [])
                existing_index = None
                for i, entry in enumerate(shared_list):
                    if entry.get(id_field) == target_id:
                        existing_index = i
                        break

                if existing_index is not None:
                    # Update existing
                     await self.permissions_collection.update_one(
                        {"_id": permission_record["_id"]},
                        {
                            "$set": {
                                f"{shared_list_field}.{existing_index}.permission": permission,
                                f"{shared_list_field}.{existing_index}.updated_at": now,
                                "updated_at": now
                            }
                        }
                    )
                     action = "updated"
                else:
                    # Add new
                    share_entry = {
                        id_field: target_id,
                        "permission": permission,
                        "shared_by": owner_id,
                        "shared_at": now,
                    }
                    # Only add context team_id for user shares (avoid overwriting team_id key for team shares)
                    if target_type == "user" and team_id:
                        share_entry["team_id"] = team_id
                    await self.permissions_collection.update_one(
                        {"_id": permission_record["_id"]},
                        {
                            "$push": {shared_list_field: share_entry},
                            "$set": {"updated_at": now}
                        }
                    )
                    action = "created"

            else:
                 # Create new record
                is_owner = await self._verify_resource_ownership(owner_id, resource_id, resource_type)
                if not is_owner:
                    return {"success": False, "error": "Resource not found or you are not the owner"}
                    
                new_record = {
                    "resource_id": resource_id,
                    "resource_type": resource_type,
                    "owner_id": owner_id,
                    "team_id": team_id,
                    "shared_with": [],
                    "shared_with_teams": [],
                    "created_at": now,
                    "updated_at": now
                }
                
                share_entry = {
                    id_field: target_id,
                    "permission": permission,
                    "shared_by": owner_id,
                    "shared_at": now,
                }
                # Only add context team_id for user shares (avoid overwriting team_id key for team shares)
                if target_type == "user" and team_id:
                    share_entry["team_id"] = team_id
                
                new_record[shared_list_field] = [share_entry]
                
                await self.permissions_collection.insert_one(new_record)
                action = "created"
            
            # Log the action
            await self._log_action(
                action="grant_access",
                actor_id=owner_id,
                resource_id=resource_id,
                resource_type=resource_type,
                target_id=target_id,
                target_type=target_type,
                permission=permission,
                team_id=team_id
            )
            
            logger.info(f"✅ Access {action}: {target_id} now has {permission} access to {resource_type}:{resource_id}")
            
            return {
                "success": True,
                "message": f"Access {action} successfully",
                "permission": permission
            }
            
        except Exception as e:
            logger.error(f"❌ Error granting access: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"Failed to grant access: {str(e)}"
            }
    
    async def _verify_resource_ownership(
        self,
        user_id: str,
        resource_id: str,
        resource_type: str
    ) -> bool:
        """Verify that user owns the resource in the original collection."""
        try:
            legacy_collections = {
                "presentation": "presentations",
                "report": "composer_reports",
                "diagram": "diagrams",
                "vault": "folders",
                "document": "document_chunked",
                "chat": "chat_sessions",
                "project": "projects",
                "printable": "printables",
            }
            
            collection_name = legacy_collections.get(resource_type)
            if not collection_name:
                return False
            
            collection = self.db[collection_name]
            
            # Try both ObjectId and string ID
            resource = await collection.find_one({"_id": resource_id, "user_id": user_id})
            if not resource:
                try:
                    resource = await collection.find_one({"_id": ObjectId(resource_id), "user_id": user_id})
                except (InvalidId, TypeError):
                    # resource_id isn't a valid ObjectId → the string-_id lookup
                    # above already ran. A real DB error is NOT caught here; it
                    # propagates to the outer handler (logs + fails closed).
                    pass
            
            return resource is not None
            
        except Exception as e:
            logger.error(f"❌ Error verifying ownership: {e}")
            return False
    
    # =========================================================================
    # Permission Revoking
    # =========================================================================
    
    async def revoke_access(
        self,
        revoker_id: str,
        resource_id: str,
        resource_type: str,
        target_id: str,
        target_type: str = "user"
    ) -> Dict[str, Any]:
        """
        Revoke a user or team's access to a resource.
        
        Args:
            revoker_id: User revoking access (must be owner or admin)
            resource_id: Resource to revoke access from
            resource_type: Type of resource
            target_id: ID of user or team to revoke access from
            target_type: "user" or "team"
            
        Returns:
            Dict with success status and message
        """
        try:
            # Handle backward compatibility (if target_id came as target_user_id kwarg)
            # Python works but types might be weird. Assuming positional or updated keywords.
            
            logger.info(f"🔓 Revoking access: {target_type}={target_id} from {resource_type}:{resource_id}")
            
            # Get permission record
            permission_record = await self.permissions_collection.find_one({
                "resource_id": resource_id,
                "resource_type": resource_type
            })
            
            if not permission_record:
                return {
                    "success": False,
                    "error": "Permission record not found"
                }
            
            # Verify revoker has admin access
            record_owner = permission_record.get("owner_id")
            if record_owner != revoker_id:
                access_check = await self.check_access(revoker_id, resource_id, resource_type, "admin")
                if not access_check.get("allowed"):
                    return {
                        "success": False,
                        "error": "Only the owner or admins can revoke access"
                    }
            
            # Remove from shared list
            if target_type == "user":
                update_query = {"$pull": {"shared_with": {"user_id": target_id}}}
                
                # Also remove from legacy shared_with_me collection
                await self.db["shared_with_me"].delete_one({
                    "user_id": target_id,
                    "source_id": resource_id,
                    "content_type": resource_type
                })
            else: # team
                update_query = {"$pull": {"shared_with_teams": {"team_id": target_id}}}

            result = await self.permissions_collection.update_one(
                {"_id": permission_record["_id"]},
                {
                    **update_query,
                    "$set": {"updated_at": datetime.utcnow()}
                }
            )
            
            if result.modified_count == 0:
                return {
                    "success": False,
                    "error": f"{target_type.capitalize()} did not have access to this resource"
                }
            
            # Log the action
            await self._log_action(
                action="revoke_access",
                actor_id=revoker_id,
                resource_id=resource_id,
                resource_type=resource_type,
                target_id=target_id,
                target_type=target_type
            )
            
            logger.info(f"✅ Access revoked: {target_type}={target_id} no longer has access to {resource_type}:{resource_id}")
            
            return {
                "success": True,
                "message": "Access revoked successfully"
            }
            
        except Exception as e:
            logger.error(f"❌ Error revoking access: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"Failed to revoke access: {str(e)}"
            }
    
    async def revoke_all_user_access_in_workspace(
        self,
        team_id: str,
        user_id: str,
        revoked_by: str
    ) -> Dict[str, Any]:
        """
        Revoke ALL shared access for a user in a specific workspace.
        
        This is called when a user is removed from a workspace to ensure
        they lose access to all shared resources in that workspace.
        
        Args:
            team_id: The workspace ID
            user_id: The user being removed
            revoked_by: The user performing the removal
            
        Returns:
            Dict with success status and count of revoked permissions
        """
        try:
            logger.info(f"🔓 Revoking ALL access for user {user_id} in workspace {team_id}")
            
            # Find all permission records where user has access in this workspace
            # This includes resources where team_id matches OR shared_with entry has team_id
            
            revoked_count = 0
            
            # Method 1: Remove from shared_with arrays where team_id matches
            result = await self.permissions_collection.update_many(
                {"team_id": team_id},
                {
                    "$pull": {"shared_with": {"user_id": user_id}},
                    "$set": {"updated_at": datetime.utcnow()}
                }
            )
            revoked_count += result.modified_count
            
            # Method 2: Remove from shared_with arrays where shared entry has team_id
            result = await self.permissions_collection.update_many(
                {"shared_with.user_id": user_id, "shared_with.team_id": team_id},
                {
                    "$pull": {"shared_with": {"user_id": user_id, "team_id": team_id}},
                    "$set": {"updated_at": datetime.utcnow()}
                }
            )
            revoked_count += result.modified_count
            
            # Also clean up legacy shared_with_me collection
            legacy_result = await self.db["shared_with_me"].delete_many({
                "user_id": user_id,
                "team_id": team_id
            })
            
            # Log the bulk action
            await self._log_action(
                action="bulk_revoke_workspace",
                actor_id=revoked_by,
                resource_id=team_id,
                resource_type="workspace",
                target_id=user_id,
                metadata={"revoked_count": revoked_count}
            )
            
            logger.info(f"✅ Revoked {revoked_count} shared access entries for user {user_id} in workspace {team_id}")
            
            return {
                "success": True,
                "message": f"Revoked all workspace access",
                "revoked_count": revoked_count,
                "legacy_removed": legacy_result.deleted_count
            }
            
        except Exception as e:
            logger.error(f"❌ Error revoking workspace access: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"Failed to revoke workspace access: {str(e)}"
            }
    
    # =========================================================================
    # Resource Listing with Permission Filtering
    # =========================================================================
    
    async def get_accessible_resources(
        self,
        user_id: str,
        resource_type: str,
        team_id: Optional[str] = None,
        include_owned: bool = True,
        include_shared: bool = True
    ) -> Dict[str, Any]:
        """
        Get all resources of a type that a user can access.
        
        Args:
            user_id: The user requesting resources
            resource_type: Type of resources to fetch
            team_id: Optional workspace filter
            include_owned: Include resources owned by user
            include_shared: Include resources shared with user
            
        Returns:
            Dict with owned_ids and shared_ids lists
        """
        try:
            logger.info(f"📋 Getting accessible {resource_type} for user {user_id}, team={team_id}")
            
            owned_ids = []
            shared_ids = []
            shared_details = []
            
            # Resolve user's MongoDB ObjectId for backward compatibility
            # (older shares may have stored ObjectId instead of email)
            user_oid = None
            try:
                user_doc = await self.db.users.find_one({"email": user_id}, {"_id": 1})
                if user_doc:
                    user_oid = str(user_doc["_id"])
            except Exception:
                pass
            
            # Build query for permission records
            query = {"resource_type": resource_type}
            
            if team_id:
                query["$or"] = [
                    {"team_id": team_id},
                    {"shared_with.team_id": team_id}
                ]
            
            # Find all permission records
            # Resolve user's team memberships for team-based sharing
            user_team_ids = []
            if include_shared:
                try:
                    user_teams = await self.team_members.find({
                        "user_id": user_id,
                        "status": "active"
                    }).to_list(length=100)
                    user_team_ids = [str(t.get("team_id")) for t in user_teams]
                except Exception as team_err:
                    logger.warning(f"⚠️ Could not resolve team memberships: {team_err}")
            
            cursor = self.permissions_collection.find(query)
            
            async for record in cursor:
                resource_id = record.get("resource_id")
                owner_id = record.get("owner_id")
                
                # Check if user owns this resource
                if include_owned and owner_id == user_id:
                    owned_ids.append(resource_id)
                    continue
                
                # Check if resource is shared with user (direct sharing)
                if include_shared:
                    found_shared = False
                    shared_with = record.get("shared_with", [])
                    for share_entry in shared_with:
                        entry_uid = share_entry.get("user_id")
                        # Match by email (new shares) or ObjectId (legacy shares)
                        if entry_uid == user_id or (user_oid and entry_uid == user_oid):
                            shared_ids.append(resource_id)
                            shared_details.append({
                                "resource_id": resource_id,
                                "permission": share_entry.get("permission", "read"),
                                "shared_by": share_entry.get("shared_by"),
                                "shared_at": share_entry.get("shared_at"),
                                "owner_id": owner_id
                            })
                            found_shared = True
                            break
                    
                    # Check team-based sharing if not already found via direct sharing
                    if not found_shared and user_team_ids:
                        shared_with_teams = record.get("shared_with_teams", [])
                        for team_share in shared_with_teams:
                            if team_share.get("team_id") in user_team_ids:
                                shared_ids.append(resource_id)
                                shared_details.append({
                                    "resource_id": resource_id,
                                    "permission": team_share.get("permission", "read"),
                                    "shared_by": team_share.get("shared_by"),
                                    "shared_at": team_share.get("shared_at"),
                                    "owner_id": owner_id,
                                    "shared_via_team": team_share.get("team_id")
                                })
                                break
            
            logger.info(f"✅ Found {len(owned_ids)} owned and {len(shared_ids)} shared {resource_type} resources")
            
            return {
                "success": True,
                "owned_ids": owned_ids,
                "shared_ids": shared_ids,
                "shared_details": shared_details,
                "total": len(owned_ids) + len(shared_ids)
            }
            
        except Exception as e:
            logger.error(f"❌ Error getting accessible resources: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "owned_ids": [],
                "shared_ids": [],
                "shared_details": []
            }
    
    async def get_resource_permissions(
        self,
        resource_id: str,
        resource_type: str,
        requester_id: str
    ) -> Dict[str, Any]:
        """
        Get all permissions for a specific resource.
        
        Args:
            resource_id: The resource to check
            resource_type: Type of resource
            requester_id: User requesting the info (must be owner or admin)
            
        Returns:
            Dict with owner and shared_with list
        """
        try:
            # First verify requester has admin access
            access_check = await self.check_access(requester_id, resource_id, resource_type, "admin")
            if not access_check.get("allowed") and not access_check.get("is_owner"):
                return {
                    "success": False,
                    "error": "Only owners and admins can view permission details"
                }
            
            permission_record = await self.permissions_collection.find_one({
                "resource_id": resource_id,
                "resource_type": resource_type
            })
            
            if not permission_record:
                return {
                    "success": True,
                    "owner_id": requester_id,  # Assume requester is owner if no record
                    "shared_with": [],
                    "team_id": None
                }
            
            return {
                "success": True,
                "owner_id": permission_record.get("owner_id"),
                "team_id": permission_record.get("team_id"),
                "shared_with": permission_record.get("shared_with", []),
                "created_at": permission_record.get("created_at"),
                "updated_at": permission_record.get("updated_at")
            }
            
        except Exception as e:
            logger.error(f"❌ Error getting resource permissions: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }
    
    # =========================================================================
    # Permission Registration (for new resources)
    # =========================================================================
    
    async def register_resource(
        self,
        resource_id: str,
        resource_type: str,
        owner_id: str,
        team_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Register a new resource in the permission system.
        
        This should be called when a new resource is created.
        
        Args:
            resource_id: The new resource's ID
            resource_type: Type of resource
            owner_id: The user creating/owning the resource
            team_id: Optional workspace ID
            
        Returns:
            Dict with success status
        """
        try:
            logger.info(f"📝 Registering new {resource_type}: {resource_id} for user {owner_id}")
            
            # Check if already registered
            existing = await self.permissions_collection.find_one({
                "resource_id": resource_id,
                "resource_type": resource_type
            })
            
            if existing:
                logger.warning(f"⚠️ Resource already registered: {resource_type}:{resource_id}")
                return {
                    "success": True,
                    "message": "Resource already registered"
                }
            
            now = datetime.utcnow()
            
            permission_record = {
                "resource_id": resource_id,
                "resource_type": resource_type,
                "owner_id": owner_id,
                "team_id": team_id,
                "shared_with": [],
                "created_at": now,
                "updated_at": now
            }
            
            await self.permissions_collection.insert_one(permission_record)
            
            logger.info(f"✅ Registered {resource_type}: {resource_id}")
            
            return {
                "success": True,
                "message": "Resource registered successfully"
            }
            
        except Exception as e:
            logger.error(f"❌ Error registering resource: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }
    
    async def unregister_resource(
        self,
        resource_id: str,
        resource_type: str,
        owner_id: str
    ) -> Dict[str, Any]:
        """
        Remove a resource from the permission system.
        
        This should be called when a resource is deleted.
        
        Args:
            resource_id: The resource being deleted
            resource_type: Type of resource
            owner_id: The user deleting (must be owner)
            
        Returns:
            Dict with success status
        """
        try:
            logger.info(f"🗑️ Unregistering {resource_type}: {resource_id}")
            
            result = await self.permissions_collection.delete_one({
                "resource_id": resource_id,
                "resource_type": resource_type,
                "owner_id": owner_id
            })
            
            if result.deleted_count == 0:
                logger.warning(f"⚠️ No permission record found for {resource_type}:{resource_id}")
            
            # Also clean up any shared_with_me entries
            await self.db["shared_with_me"].delete_many({
                "source_id": resource_id,
                "content_type": resource_type
            })
            
            # Log the action
            await self._log_action(
                action="unregister_resource",
                actor_id=owner_id,
                resource_id=resource_id,
                resource_type=resource_type
            )
            
            return {
                "success": True,
                "message": "Resource unregistered successfully"
            }
            
        except Exception as e:
            logger.error(f"❌ Error unregistering resource: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }
    
    # =========================================================================
    # Audit Logging
    # =========================================================================
    
    async def _log_action(
        self,
        action: str,
        actor_id: str,
        resource_id: str,
        resource_type: str,
        target_id: Optional[str] = None,
        target_type: str = "user",
        permission: Optional[str] = None,
        team_id: Optional[str] = None,
        metadata: Optional[Dict] = None
    ):
        """Log an authorization action for audit purposes."""
        try:
            log_entry = {
                "action": action,
                "actor_id": actor_id,
                "resource_id": resource_id,
                "resource_type": resource_type,
                "target_id": target_id,
                "target_type": target_type,
                "permission": permission,
                "team_id": team_id,
                "metadata": metadata,
                "timestamp": datetime.utcnow()
            }
            
            await self.audit_log.insert_one(log_entry)
            
        except Exception as e:
            logger.error(f"❌ Failed to log audit action: {e}")


# =========================================================================
# Singleton Instance
# =========================================================================

_authorization_service: Optional[AuthorizationService] = None


def get_authorization_service() -> AuthorizationService:
    """Get the singleton AuthorizationService instance."""
    global _authorization_service
    if _authorization_service is None:
        _authorization_service = AuthorizationService()
    return _authorization_service


async def initialize_authorization_service(db=None) -> AuthorizationService:
    """Initialize the AuthorizationService with optional database."""
    global _authorization_service
    _authorization_service = AuthorizationService(db)
    
    # Ensure indexes for performance
    await _authorization_service.permissions_collection.create_index(
        [("resource_id", 1), ("resource_type", 1)],
        unique=True
    )
    await _authorization_service.permissions_collection.create_index(
        [("owner_id", 1), ("resource_type", 1)]
    )
    await _authorization_service.permissions_collection.create_index(
        [("team_id", 1)]
    )
    await _authorization_service.permissions_collection.create_index(
        [("shared_with.user_id", 1)]
    )
    
    logger.info("✅ AuthorizationService indexes created")
    
    return _authorization_service
