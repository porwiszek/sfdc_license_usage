"""What sflic reads from the org. Every query is a read-only SELECT.

Verification status (only add object or field names that have been run on an org):

- All seven queries (active_users, frozen_users, user_licenses,
  permission_set_licenses, psl_assignments, package_licenses,
  package_license_assignments) ran without errors on Developer Edition orgs,
  API v68.0, on 2026-11-23. package_license_assignments returned 0 rows there,
  so its fields are accepted by the API but no data has been seen yet.
- A query that fails is still recorded in the snapshot manifest and its section of
  the report says "not collected", so one wrong name does not break the run.
"""

from sflic.collect import DataDecl

USER_LICENSES = (
    "SELECT Id, Name, MasterLabel, LicenseDefinitionKey, Status, TotalLicenses, UsedLicenses "
    "FROM UserLicense"
)

PERMISSION_SET_LICENSES = (
    "SELECT Id, DeveloperName, MasterLabel, Status, TotalLicenses, UsedLicenses, ExpirationDate "
    "FROM PermissionSetLicense"
)

# Managed-package seats. AllowedLicenses is -1 for a site-wide license.
PACKAGE_LICENSES = (
    "SELECT Id, NamespacePrefix, Status, AllowedLicenses, UsedLicenses, ExpirationDate "
    "FROM PackageLicense"
)

ACTIVE_USERS = (
    "SELECT Id, Username, Name, UserType, LastLoginDate, CreatedDate, "
    "Profile.Name, Profile.UserLicenseId, Profile.UserLicense.Name "
    "FROM User WHERE IsActive = true"
)

PSL_ASSIGNMENTS = (
    "SELECT AssigneeId, PermissionSetLicenseId FROM PermissionSetLicenseAssign "
    "WHERE Assignee.IsActive = true"
)

PACKAGE_LICENSE_ASSIGNMENTS = "SELECT UserId, PackageLicenseId FROM UserPackageLicense"

# A frozen user cannot log in but still holds its licenses.
FROZEN_USERS = "SELECT UserId FROM UserLogin WHERE IsFrozen = true"

DATA: DataDecl = {
    "soql": {
        "user_licenses": USER_LICENSES,
        "permission_set_licenses": PERMISSION_SET_LICENSES,
        "package_licenses": PACKAGE_LICENSES,
        "active_users": ACTIVE_USERS,
        "psl_assignments": PSL_ASSIGNMENTS,
        "package_license_assignments": PACKAGE_LICENSE_ASSIGNMENTS,
        "frozen_users": FROZEN_USERS,
    }
}
