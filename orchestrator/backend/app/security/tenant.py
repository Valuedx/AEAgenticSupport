from fastapi import Header, HTTPException


async def get_tenant_id(x_tenant_id: str = Header(...)) -> str:
    """Extract tenant_id from request header. In production, this would
    validate a JWT and extract the tenant claim instead."""
    if not x_tenant_id:
        raise HTTPException(status_code=401, detail="Missing X-Tenant-Id header")
    return x_tenant_id
