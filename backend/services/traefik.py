"""Traefik file provider helper — writes dynamic YAML config files that Traefik watches."""

import yaml
from pathlib import Path

from config import TRAEFIK_DYNAMIC_DIR


def write_route(filename: str, routers: dict, services: dict):
    """Write a Traefik dynamic config YAML file."""
    config = {"http": {"routers": routers, "services": services}}
    filepath = TRAEFIK_DYNAMIC_DIR / filename
    filepath.write_text(yaml.dump(config, default_flow_style=False))


def remove_route(filename: str):
    """Remove a Traefik dynamic config file."""
    filepath = TRAEFIK_DYNAMIC_DIR / filename
    try:
        filepath.unlink(missing_ok=True)
    except Exception:
        pass


def write_subdomain_route(subdomain: str, base_domain: str, container_name: str, container_port: int):
    """Write a Traefik file-provider route for a subdomain -> container."""
    route_name = f"subdomain-{subdomain}"
    filename = f"{route_name}.yml"
    routers = {
        route_name: {
            "rule": f"Host(`{subdomain}.{base_domain}`)",
            "service": route_name,
            "entryPoints": ["web"],
        }
    }
    services = {
        route_name: {
            "loadBalancer": {
                "servers": [{"url": f"http://{container_name}:{container_port}"}]
            }
        }
    }
    write_route(filename, routers, services)


def remove_subdomain_route(subdomain: str):
    """Remove a Traefik subdomain route file."""
    remove_route(f"subdomain-{subdomain}.yml")


def write_deploy_route(deploy_id: str, container_name: str, container_port: int):
    """Write a Traefik file-provider route for /deploy/{id}/ path-based routing."""
    route_name = f"deploy-{deploy_id}"
    filename = f"{route_name}.yml"
    routers = {
        route_name: {
            "rule": f"PathPrefix(`/deploy/{deploy_id}/`)",
            "service": route_name,
            "entryPoints": ["web"],
            "priority": 50,
            "middlewares": [f"strip-deploy-{deploy_id}"],
        }
    }
    services = {
        route_name: {
            "loadBalancer": {
                "servers": [{"url": f"http://{container_name}:{container_port}"}]
            }
        }
    }
    middlewares = {
        f"strip-deploy-{deploy_id}": {
            "stripPrefix": {
                "prefixes": [f"/deploy/{deploy_id}"]
            }
        }
    }
    config = {"http": {"routers": routers, "services": services, "middlewares": middlewares}}
    filepath = TRAEFIK_DYNAMIC_DIR / filename
    filepath.write_text(yaml.dump(config, default_flow_style=False))


def remove_deploy_route(deploy_id: str):
    """Remove a Traefik deploy route file."""
    remove_route(f"deploy-{deploy_id}.yml")


def write_domain_route(route_id: str, domain: str, upstream: str):
    """Write a Traefik file-provider route for a custom domain."""
    filename = f"{route_id}.yml"
    routers = {
        route_id: {
            "rule": f"Host(`{domain}`)",
            "service": route_id,
            "entryPoints": ["web"],
            "priority": 200,
        }
    }
    services = {
        route_id: {
            "loadBalancer": {
                "servers": [{"url": f"http://{upstream}"}]
            }
        }
    }
    write_route(filename, routers, services)


def remove_domain_route(route_id: str):
    """Remove a Traefik domain route file."""
    remove_route(f"{route_id}.yml")
