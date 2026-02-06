"""BHL CLI - command-line interface for BountyHound Local."""

import json
import sys
import click
import yaml
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()


@click.group()
def cli():
    """BountyHound Local - Autonomous Bug Bounty Hunting Swarm"""
    pass


@cli.command()
@click.argument("domain")
@click.option("--platform", default="private", help="Bug bounty platform")
@click.option("--priority", default=5, type=int, help="Priority 1-10")
@click.option("--bounty-min", default=0, type=int, help="Minimum bounty USD")
@click.option("--bounty-max", default=10000, type=int, help="Maximum bounty USD")
@click.option("--notes", default="", help="Notes about the target")
def add(domain, platform, priority, bounty_min, bounty_max, notes):
    """Add a target to the hunt list."""
    from src.database.models import TargetDB, init_db
    init_db()
    target_id = TargetDB.add(domain, platform, priority=priority,
                             bounty_min=bounty_min, bounty_max=bounty_max, notes=notes)
    console.print(f"[green]Target added:[/green] {domain} (id: {target_id}, priority: {priority})")


@cli.command()
def targets():
    """List all targets."""
    from src.database.models import TargetDB, init_db
    init_db()
    all_targets = TargetDB.list_all()

    table = Table(title="Targets")
    table.add_column("ID", style="dim")
    table.add_column("Domain", style="green")
    table.add_column("Platform")
    table.add_column("Priority", justify="center")
    table.add_column("Findings", justify="center")
    table.add_column("Last Hunt")
    table.add_column("Status")

    for t in all_targets:
        table.add_row(
            str(t["id"]), t["domain"], t["platform"],
            str(t["priority"]), str(t["total_findings"]),
            t.get("last_full_hunt_at", "Never") or "Never",
            t["status"]
        )
    console.print(table)


@cli.command()
@click.argument("domain")
def hunt(domain):
    """Start a hunt on a specific target."""
    from src.database.models import TargetDB, init_db
    init_db()
    target = TargetDB.get(domain)
    if not target:
        console.print(f"[red]Target not found:[/red] {domain}. Run 'bhl add {domain}' first.")
        return

    from src.orchestrator.brain import run_hunt
    task = run_hunt.delay(target["id"])
    console.print(f"[green]Hunt dispatched:[/green] {domain} (task: {task.id})")
    console.print(f"Monitor at: http://localhost:8000")


@cli.command()
def swarm():
    """Start the autonomous swarm - hunts all targets by priority."""
    from src.orchestrator.brain import run_swarm
    task = run_swarm.delay()
    console.print(f"[green]Swarm started:[/green] task {task.id}")
    console.print(f"Dashboard: http://localhost:8000")
    console.print(f"Flower: http://localhost:5555")


@cli.command()
@click.argument("domain")
def recon(domain):
    """Run recon only on a domain."""
    from src.database.models import TargetDB, HuntDB, init_db
    init_db()
    target = TargetDB.get(domain)
    if not target:
        target_id = TargetDB.add(domain)
    else:
        target_id = target["id"]

    from src.workers.recon import run_recon
    hunt_id = HuntDB.create(target_id, "recon_only")
    task = run_recon.delay(target_id, hunt_id, domain)
    console.print(f"[green]Recon dispatched:[/green] {domain} (task: {task.id})")


@cli.command()
def status():
    """Show system status."""
    from src.database.models import TargetDB, HuntDB, init_db
    from src.database.redis_manager import TaskQueue
    init_db()

    targets = TargetDB.list_all()
    active = HuntDB.get_active()
    stats = TaskQueue.get_stats()

    panel = Panel(
        f"[green]Targets:[/green] {len(targets)}\n"
        f"[yellow]Active Hunts:[/yellow] {len(active)}\n"
        f"[cyan]Hunts Completed:[/cyan] {stats.get('hunts_completed', 0)}\n"
        f"[red]Total Findings:[/red] {stats.get('findings_total', 0)}\n"
        f"\n[dim]Dashboard: http://localhost:8000[/dim]\n"
        f"[dim]Flower: http://localhost:5555[/dim]",
        title="BountyHound Local Status",
        border_style="green"
    )
    console.print(panel)


@cli.command()
@click.argument("action", type=click.Choice(["list", "show", "refresh"]))
@click.argument("domain", required=False)
def creds(action, domain):
    """Manage credentials (list, show <domain>, refresh <domain>)."""
    from src.services.credential_manager import list_targets_with_creds, load_credentials, mask_value

    if action == "list":
        targets = list_targets_with_creds()
        table = Table(title="Saved Credentials")
        table.add_column("Target", style="green")
        table.add_column("User A")
        table.add_column("User B")
        table.add_column("A Expired?")
        table.add_column("B Expired?")
        for t in targets:
            table.add_row(
                t["target"],
                "Yes" if t["has_user_a"] else "No",
                "Yes" if t["has_user_b"] else "No",
                "[red]Yes[/red]" if t["user_a_expired"] else "[green]No[/green]",
                "[red]Yes[/red]" if t["user_b_expired"] else "[green]No[/green]",
            )
        console.print(table)

    elif action == "show":
        if not domain:
            console.print("[red]Usage:[/red] bhl creds show <domain>")
            return
        creds_data = load_credentials(domain)
        if not creds_data:
            console.print(f"[red]No credentials for {domain}[/red]")
            return
        sensitive = ["PASSWORD", "TOKEN", "COOKIE", "CSRF", "SECRET"]
        for k, v in creds_data.items():
            if any(s in k.upper() for s in sensitive):
                console.print(f"  {k} = {mask_value(v)}")
            else:
                console.print(f"  {k} = {v}")

    elif action == "refresh":
        if not domain:
            console.print("[red]Usage:[/red] bhl creds refresh <domain>")
            return
        from src.workers.auth import refresh_tokens
        task = refresh_tokens.delay(domain)
        console.print(f"[green]Token refresh dispatched:[/green] {domain} (task: {task.id})")


@cli.command()
def health():
    """Check health of all model servers."""
    try:
        from src.models.vllm_client import get_llm
        llm = get_llm()
        status = llm.health_check()
        for model, state in status.items():
            color = "green" if state == "healthy" else "red"
            console.print(f"  [{color}]{model}[/{color}]: {state}")
    except Exception as e:
        console.print(f"[red]Health check failed:[/red] {e}")


@cli.command()
def load():
    """Load targets from config/targets.yaml."""
    from src.database.models import TargetDB, init_db
    init_db()

    config_path = Path(__file__).parent / "config" / "targets.yaml"
    if not config_path.exists():
        console.print("[red]config/targets.yaml not found[/red]")
        return

    with open(config_path) as f:
        config = yaml.safe_load(f)

    targets = config.get("targets", [])
    if not targets:
        console.print("[yellow]No targets in config/targets.yaml[/yellow]")
        return

    for t in targets:
        target_id = TargetDB.add(
            domain=t["domain"],
            platform=t.get("platform", "private"),
            priority=t.get("priority", 5),
            bounty_min=t.get("bounty_range", [0, 0])[0] if t.get("bounty_range") else 0,
            bounty_max=t.get("bounty_range", [0, 0])[1] if t.get("bounty_range") else 0,
            scope=t.get("scope", {}),
            notes=t.get("notes", ""),
        )
        console.print(f"  [green]+[/green] {t['domain']} (priority: {t.get('priority', 5)})")

    console.print(f"\n[green]Loaded {len(targets)} targets[/green]")


if __name__ == "__main__":
    cli()
