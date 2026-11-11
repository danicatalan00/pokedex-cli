"""Motor de combate: cada turno es una función pura ``(estado, acción, azar) → resultado``.

El motor no imprime ni guarda nada: recibe un ``BattleState`` inmutable y
devuelve el estado siguiente junto con los eventos que lo cuentan, en orden.
Los eventos bastan para narrar y animar el turno entero: la presentación no
necesita mirar estados intermedios.

Orden de un turno (Gen 3): huida y cambios; movimientos por prioridad y
velocidad; fin de turno (quemadura, veneno, Drenadoras, atrapado, pantallas);
y relevos de los debilitados. Un Pokémon debilitado ya no actúa en ese turno.
El combate acaba en cuanto un lado se queda sin Pokémon; si se quedan los dos
a la vez, en tablas, como hace ``checkteamslost`` en Gen 3.

Azar que consume, en este orden: la IA rival (si no llega su acción), el
orden del turno (solo con empate total) y, por cada acción: descongelación,
confusión, parálisis, Protección encadenada, precisión, número de golpes,
crítico y daño de cada golpe, efectos secundarios y sus duraciones, y el
relevo forzado de Rugido contra un entrenador. El fin de turno no tira dados.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from types import MappingProxyType
from typing import Any

from pokedex_cli.domain.battle.ai import FIRST_TURN_ONLY, choose_action, choose_replacement_index
from pokedex_cli.domain.battle.combatants import level_up, refresh_auto_moveset
from pokedex_cli.domain.battle.mechanics import (
    MAX_ROLL,
    MIN_ROLL,
    calculate_damage,
    can_receive_status,
    confusion_damage,
    escape_succeeds,
    experience_yield,
    fixed_damage,
    move_effectiveness,
    roll_critical,
    roll_hit_count,
    roll_hits,
    turn_order,
)
from pokedex_cli.domain.battle.model import (
    MAX_TURNS,
    SIDES,
    STAGE_KEYS,
    Action,
    BattleEnded,
    BattleEvent,
    BattleKind,
    BattleStarted,
    BattleState,
    Charging,
    Combatant,
    Confused,
    ConfusionCheck,
    ConfusionEnded,
    ConfusionHurt,
    DamageCause,
    DamageClass,
    Damaged,
    EscapeAttempted,
    ExperienceGained,
    FailReason,
    Fainted,
    ForcedOut,
    HealCause,
    Healed,
    HitCount,
    ImmobileReason,
    Immobilized,
    LeveledUp,
    Move,
    MoveFailed,
    MoveLearned,
    MoveMissed,
    MoveUsed,
    NoEffect,
    NoMovesLeft,
    OneHitKO,
    Outcome,
    Phase,
    Protected,
    ProtectionRaised,
    PumpedUp,
    RandomSource,
    Recalled,
    Released,
    ReplacementNeeded,
    Run,
    ScreenFaded,
    ScreenRaised,
    Seeded,
    SentOut,
    SideId,
    SpendTurn,
    Stages,
    StatChange,
    StatChanged,
    StatsReset,
    StatUnchanged,
    Status,
    StatusBlocked,
    StatusCured,
    StatusInflicted,
    SwitchTo,
    Teleported,
    Trapped,
    TurnResult,
    TurnStarted,
    UseMove,
    Volatile,
    other_side,
)
from pokedex_cli.domain.battle.moves import (
    CHARGE_MOVES,
    DREAM_EATER,
    FALSE_SWIPE,
    FOCUS_ENERGY,
    FORCE_SWITCH,
    HAZE,
    PROTECT_MOVES,
    RECHARGE_MOVES,
    REST,
    SACRIFICE,
    SCREENS,
    SELF_TARGETS,
    SEMI_INVULNERABLE,
    SPLASH,
    STRUGGLE,
    TELEPORT,
    TOXIC_MOVES,
    hit_range,
    is_fixed_damage,
    is_supported,
)
from pokedex_cli.domain.progression import (
    MAX_LEVEL,
    experience_for_level,
    level_for_experience,
)

PARALYSIS_CHANCE = 0.25
THAW_CHANCE = 0.2
CONFUSION_HURT_CHANCE = 0.5
# Turnos sin poder actuar por sueño; al siguiente se despierta y actúa.
SLEEP_TURNS = (1, 3)
REST_SLEEP_TURNS = 2
# Contador de confusión de Gen 3: se descuenta al intentar moverse y, al
# llegar a 0, se le pasa y actúa (de 1 a 4 turnos confuso).
CONFUSION_TURNS = (2, 5)
# Turnos de daño de Constricción y compañía si el movimiento no los trae.
TRAP_TURNS = (2, 5)
SCREEN_TURNS = 5
# Tóxico: n/16 de los PS máximos, con n de 1 a 15.
TOXIC_MAX_COUNTER = 15
# Protección encadenada: 1, 1/2, 1/4 y 1/8 como mínimo.
PROTECT_CHAIN_LIMIT = 3
# Daño residual como fracción de los PS máximos (mínimo 1).
BURN_DIVISOR = 8
POISON_DIVISOR = 8
TOXIC_DIVISOR = 16
LEECH_SEED_DIVISOR = 8
TRAP_DIVISOR = 16

_AILMENT_STATUS: Mapping[str, Status] = MappingProxyType(
    {
        "paralysis": Status.PARALYSIS,
        "sleep": Status.SLEEP,
        "freeze": Status.FREEZE,
        "burn": Status.BURN,
        "poison": Status.POISON,
    }
)
# Problemas que no son de estado persistente y sí sabe aplicar el motor.
_VOLATILE_AILMENTS: frozenset[str] = frozenset({"confusion", "trap", "leech-seed"})
# Objetivos que no son el rival: el usuario, su lado, un aliado o el campo.
# Lo que no está aquí (``selected-pokemon``, ``all-opponents``…) apunta al rival.
_NOT_FOE_TARGETS: frozenset[str] = SELF_TARGETS | {
    "ally",
    "entire-field",
    "opponents-field",
    "specific-move",
    "fainting-pokemon",
}


# --- Consultas --------------------------------------------------------------


def available_moves(c: Combatant) -> tuple[int, ...]:
    """Índices de los movimientos que aún tienen PP."""
    return tuple(index for index, slot in enumerate(c.moves) if slot.pp > 0)


def switch_targets(state: BattleState, side: SideId) -> tuple[int, ...]:
    """Miembros vivos de ``side`` que no son el activo."""
    team = state.side(side)
    return tuple(index for index in team.able if index != team.active)


def can_run(state: BattleState) -> bool:
    """Solo se puede huir de un combate salvaje."""
    return state.kind is BattleKind.WILD


def locked_in(state: BattleState, side: SideId) -> bool:
    """¿El activo de ``side`` tiene el turno comprometido (carga o recarga)?

    Entonces el motor ignora el movimiento elegido y sigue con lo que tiene
    en curso; la interfaz no debería ofrecer el menú, y cambiar, huir o
    gastar el turno no se admite.
    """
    return _is_locked(state.side(side).current)


def action_error(state: BattleState, action: Action, side: SideId = "player") -> str | None:
    """Motivo en español por el que ``action`` no vale ahora; ``None`` si vale.

    Huir contra un entrenador sí vale: el motor responde con
    ``EscapeAttempted(forbidden=True)`` sin gastar el turno.
    """
    if state.finished:
        return "El combate ya ha terminado."
    if state.phase is Phase.CHOOSE_REPLACEMENT:
        return "Antes tienes que sacar a otro Pokémon."
    active = state.side(side).current
    locked = _is_locked(active)
    if isinstance(action, UseMove):
        if locked or not available_moves(active):
            return None
        if not 0 <= action.slot < len(active.moves):
            return "Ese movimiento no existe."
        if active.moves[action.slot].pp <= 0:
            return "¡No quedan PP para este movimiento!"
        return None
    if locked:
        return f"¡{active.name} no puede hacer otra cosa este turno!"
    if isinstance(action, SwitchTo):
        return _switch_error(state, side, action.index)
    if isinstance(action, Run):
        return None if side == "player" else "Solo el jugador puede huir."
    if isinstance(action, SpendTurn):
        return None
    return "Acción desconocida."


# --- Fases del combate ------------------------------------------------------


def begin(state: BattleState) -> TurnResult:
    """Abre el combate: ``BattleStarted`` y los dos ``SentOut`` (rival primero).

    Registra que el activo del jugador se enfrenta al rival (para la EXP).
    No cambia ``turn``.
    """
    opponent = state.opponent.current
    player = state.player.current
    events: tuple[BattleEvent, ...] = (
        BattleStarted(kind=state.kind, trainer=state.opponent.trainer),
        _sent_out_event("opponent", opponent),
        _sent_out_event("player", player),
    )
    participants = _with_participant(state.participants, opponent.key, player.key)
    return TurnResult(replace(state, participants=participants), events)


def resolve_turn(
    state: BattleState,
    action: Action,
    rng: RandomSource,
    *,
    opponent_action: Action | None = None,
) -> TurnResult:
    """Resuelve un turno completo con la acción del jugador.

    Si ``opponent_action`` es ``None``, la elige ``ai.choose_action``. Una
    acción inválida (índice, PP, fase…) lanza ``ValueError`` con el motivo en
    español. Si un activo tiene el turno comprometido, su movimiento elegido
    se ignora. Huir de un entrenador devuelve el aviso sin gastar el turno.
    """
    error = action_error(state, action, "player")
    if error is not None:
        raise ValueError(error)
    if isinstance(action, Run) and not can_run(state):
        return TurnResult(state, (EscapeAttempted(success=False, forbidden=True),))
    if opponent_action is None:
        locked = locked_in(state, "opponent")
        opponent_action = UseMove(0) if locked else choose_action(state, "opponent", rng)
    error = action_error(state, opponent_action, "opponent")
    if error is not None:
        raise ValueError(f"Acción del rival no válida: {error}")
    turn = _Turn(state, rng)
    turn.play(action, opponent_action)
    return turn.result()


def choose_replacement(state: BattleState, index: int, rng: RandomSource) -> TurnResult:
    """Saca al miembro ``index`` del jugador tras un desmayo (``SentOut``).

    Solo en la fase ``CHOOSE_REPLACEMENT``; después vuelve ``CHOOSE_ACTION``
    sin gastar turno. ``rng`` no se usa: sacar a otro no tiene azar.
    """
    if state.phase is not Phase.CHOOSE_REPLACEMENT:
        raise ValueError("Ahora no hace falta sacar a otro Pokémon.")
    error = _switch_error(state, "player", index)
    if error is not None:
        raise ValueError(error)
    turn = _Turn(state, rng)
    turn.send_out("player", index)
    turn.state = replace(turn.state, phase=Phase.CHOOSE_ACTION)
    return turn.result()


def finish(state: BattleState, outcome: Outcome, events: Sequence[BattleEvent] = ()) -> TurnResult:
    """Cierra un combate decidido fuera del motor (captura, huida del rival…).

    Deja la fase ``FINISHED`` con ``outcome`` y añade ``BattleEnded`` tras
    ``events``.
    """
    if state.finished:
        raise ValueError("El combate ya ha terminado.")
    finished = replace(state, phase=Phase.FINISHED, outcome=outcome)
    return TurnResult(finished, (*events, BattleEnded(outcome=outcome)))


# --- Ayudas puras -----------------------------------------------------------


def _is_locked(c: Combatant) -> bool:
    return c.volatile.charging is not None or c.volatile.recharging


def _switch_error(state: BattleState, side: SideId, index: int) -> str | None:
    team = state.side(side)
    if not 0 <= index < len(team.team):
        return "Ese Pokémon no está en el equipo."
    member = team.team[index]
    if member.fainted:
        return f"¡{member.name} no tiene fuerzas para luchar!"
    if index == team.active:
        return f"¡{member.name} ya está combatiendo!"
    return None


def _with_participant(
    participants: tuple[tuple[str, tuple[str, ...]], ...], opponent_key: str, player_key: str
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    entries = dict(participants)
    keys = entries.get(opponent_key, ())
    if player_key in keys:
        return participants
    entries[opponent_key] = (*keys, player_key)
    return tuple(entries.items())


def _sent_out_event(side: SideId, c: Combatant) -> SentOut:
    return SentOut(
        side=side,
        key=c.key,
        name=c.name,
        species=c.species,
        form=c.form,
        shiny=c.shiny,
        level=c.level,
    )


def _targets_foe(move: Move) -> bool:
    return move.is_damaging or move.target not in _NOT_FOE_TARGETS


def _trap_turns(move: Move) -> tuple[int, int]:
    """Duración del atrapamiento: la del movimiento o 2–5 si no la trae."""
    low = move.min_turns if move.min_turns is not None else move.max_turns
    high = move.max_turns if move.max_turns is not None else move.min_turns
    if low is None or high is None or low < 1:
        return TRAP_TURNS
    return low, max(low, high)


def _slot_of(c: Combatant, slug: str) -> int | None:
    for index, slot in enumerate(c.moves):
        if slot.move.slug == slug:
            return index
    return None


def _charged_move(c: Combatant, slug: str) -> Move | None:
    """El golpe en carga: del moveset o, si lo olvidó al subir de nivel, del learnset."""
    index = _slot_of(c, slug)
    if index is not None:
        return c.moves[index].move
    learnt = [entry.move for entry in c.learnset if entry.move.slug == slug]
    return learnt[-1] if learnt else None


# --- Turno ------------------------------------------------------------------


class _Turn:
    """Acumulador privado de un turno: estado vigente, eventos y azar.

    Solo vive dentro de una llamada pública; desde fuera, el turno es una
    función pura. Cada cambio sustituye ``state`` por un estado nuevo.
    """

    def __init__(self, state: BattleState, rng: RandomSource) -> None:
        self.state = state
        self.rng = rng
        self.events: list[BattleEvent] = []
        # Lados que ya han actuado este turno: el retroceso solo afecta a
        # quien aún no se ha movido y Protección falla si se usa la última.
        self.acted: set[SideId] = set()
        # Claves ya anunciadas como debilitadas en este turno.
        self.announced: set[str] = set()
        # Slug elegido por cada lado al empezar el turno (ver ``use_move``).
        self.planned: dict[SideId, str] = {}

    def result(self) -> TurnResult:
        return TurnResult(self.state, tuple(self.events))

    # --- Estado y eventos ---------------------------------------------------

    @property
    def finished(self) -> bool:
        return self.state.finished

    def emit(self, event: BattleEvent) -> None:
        self.events.append(event)

    def active(self, side: SideId) -> Combatant:
        return self.state.side(side).current

    def put(self, side: SideId, combatant: Combatant) -> None:
        self.state = self.state.with_side(side, self.state.side(side).with_current(combatant))

    def set_volatile(self, side: SideId, **changes: Any) -> None:
        """Cambia campos de ``Volatile`` del activo de ``side``."""
        c = self.active(side)
        self.put(side, replace(c, volatile=replace(c.volatile, **changes)))

    def fail(self, side: SideId, reason: FailReason) -> None:
        user = self.active(side)
        self.emit(MoveFailed(side=side, key=user.key, name=user.name, reason=reason))

    def chance(self, percent: int, *, zero_is_certain: bool) -> bool:
        """Tirada porcentual; 100 (o 0 si ``zero_is_certain``) no consume azar."""
        if percent <= 0:
            return zero_is_certain
        if percent >= 100:
            return True
        return self.rng.random() * 100 < percent

    def damage(
        self,
        side: SideId,
        amount: int,
        cause: DamageCause,
        *,
        effectiveness: float = 1.0,
        critical: bool = False,
    ) -> int:
        """Resta PS (nunca por debajo de 0), emite ``Damaged`` y devuelve lo restado."""
        c = self.active(side)
        dealt = max(0, min(amount, c.hp))
        after = c.hp - dealt
        if dealt:
            self.put(side, replace(c, hp=after))
        self.emit(
            Damaged(
                side=side,
                key=c.key,
                name=c.name,
                amount=dealt,
                hp_before=c.hp,
                hp_after=after,
                max_hp=c.max_hp,
                cause=cause,
                effectiveness=effectiveness,
                critical=critical,
            )
        )
        return dealt

    def heal(self, side: SideId, amount: int, cause: HealCause) -> int:
        """Suma PS hasta el máximo; sin nada que curar no emite nada."""
        c = self.active(side)
        gained = 0 if c.fainted else max(0, min(amount, c.max_hp - c.hp))
        if gained:
            self.put(side, replace(c, hp=c.hp + gained))
            self.emit(
                Healed(
                    side=side,
                    key=c.key,
                    name=c.name,
                    amount=gained,
                    hp_before=c.hp,
                    hp_after=c.hp + gained,
                    max_hp=c.max_hp,
                    cause=cause,
                )
            )
        return gained

    def cure(self, side: SideId) -> None:
        c = self.active(side)
        self.put(side, replace(c, status=Status.NONE, status_turns=0))
        self.emit(StatusCured(side=side, key=c.key, name=c.name, status=c.status))

    # --- Turno completo -----------------------------------------------------

    def play(self, player_action: Action, opponent_action: Action) -> None:
        self.state = replace(self.state, turn=self.state.turn + 1)
        self.emit(TurnStarted(turn=self.state.turn))
        order = turn_order(self.state, player_action, opponent_action, self.rng)
        actions: dict[SideId, Action] = {"player": player_action, "opponent": opponent_action}
        # Quien entra a mitad de turno (arrastrado por Rugido) no hereda la
        # acción elegida para el que se fue.
        chosen_by = {side: self.active(side).key for side in SIDES}
        for side, action in actions.items():
            chooser = self.active(side)
            if isinstance(action, UseMove) and not _is_locked(chooser):
                if 0 <= action.slot < len(chooser.moves):
                    self.planned[side] = chooser.moves[action.slot].move.slug
        for side in order:
            if self.finished:
                return
            active = self.active(side)
            if active.fainted or active.key != chosen_by[side]:
                continue
            self.act(side, actions[side])
            self.acted.add(side)
            self.settle(other_side(side), side)
            self.check_end()
        if self.finished:
            return
        self.end_of_turn(order)
        if self.finished:
            return
        if self.state.turn >= MAX_TURNS:
            self.end(Outcome.DRAW)
            return
        self.replacements()

    def act(self, side: SideId, action: Action) -> None:
        if isinstance(action, UseMove):
            self.use_move(side, action.slot)
        elif isinstance(action, SwitchTo):
            self.switch(side, action.index)
        elif isinstance(action, Run):
            self.run()
        # SpendTurn: el jugador ya gastó el turno fuera del motor.

    def end(self, outcome: Outcome) -> None:
        self.state = replace(self.state, phase=Phase.FINISHED, outcome=outcome)
        self.emit(BattleEnded(outcome=outcome))

    def check_end(self) -> None:
        """Termina el combate si algún lado se ha quedado sin Pokémon."""
        if self.finished:
            return
        player_out = not self.state.player.able
        opponent_out = not self.state.opponent.able
        if player_out and opponent_out:
            self.end(Outcome.DRAW)
        elif opponent_out:
            self.end(Outcome.WON)
        elif player_out:
            self.end(Outcome.LOST)

    # --- Huida, cambios y relevos -------------------------------------------

    def run(self) -> None:
        # Gen 3 compara las velocidades sin escalones ni parálisis.
        player, wild = self.active("player"), self.active("opponent")
        attempts = self.state.escape_attempts
        if escape_succeeds(player.stats.spe, wild.stats.spe, attempts, self.rng):
            self.emit(EscapeAttempted(success=True))
            self.end(Outcome.ESCAPED)
            return
        self.emit(EscapeAttempted(success=False))
        self.state = replace(self.state, escape_attempts=attempts + 1)

    def switch(self, side: SideId, index: int) -> None:
        leaving = self.active(side)
        self.emit(Recalled(side=side, key=leaving.key, name=leaving.name))
        self.withdraw(side)
        self.send_out(side, index)

    def withdraw(self, side: SideId) -> None:
        """Quien se retira pierde escalones y estado volátil; Tóxico vuelve a 1/16."""
        c = self.active(side)
        turns = 1 if c.status is Status.TOXIC else c.status_turns
        self.put(side, replace(c, stages=Stages(), volatile=Volatile(), status_turns=turns))
        self.release_foe(side)

    def release_foe(self, side: SideId) -> None:
        """Si el activo de ``side`` se va, el rival deja de estar atrapado por él."""
        foe_side = other_side(side)
        if self.active(foe_side).volatile.trapped_turns:
            self.set_volatile(foe_side, trapped_turns=0, trapped_by=None)

    def send_out(self, side: SideId, index: int) -> None:
        self.state = self.state.with_side(side, replace(self.state.side(side), active=index))
        self.emit(_sent_out_event(side, self.active(side)))
        player, opponent = self.active("player"), self.active("opponent")
        if not player.fainted and not opponent.fainted:
            participants = _with_participant(self.state.participants, opponent.key, player.key)
            self.state = replace(self.state, participants=participants)

    def replacements(self) -> None:
        """Fin del turno: el rival saca relevo y el jugador, si le toca, elige."""
        if self.active("opponent").fainted:
            index = choose_replacement_index(self.state, "opponent", self.rng)
            self.send_out("opponent", index)
        if self.active("player").fainted:
            self.state = replace(self.state, phase=Phase.CHOOSE_REPLACEMENT)
            self.emit(ReplacementNeeded(side="player"))

    # --- Desmayos y experiencia ---------------------------------------------

    def settle(self, *sides: SideId) -> None:
        """Anuncia los desmayos pendientes, en el orden dado."""
        for side in sides:
            c = self.active(side)
            if c.fainted and c.key not in self.announced:
                self.announced.add(c.key)
                self.faint(side)

    def faint(self, side: SideId) -> None:
        c = self.active(side)
        self.emit(Fainted(side=side, key=c.key, name=c.name))
        cleared = replace(
            c, hp=0, status=Status.NONE, status_turns=0, stages=Stages(), volatile=Volatile()
        )
        self.put(side, cleared)
        self.release_foe(side)
        if side == "opponent":
            self.award_experience(c)

    def award_experience(self, defeated: Combatant) -> None:
        """EXP de Gen 3 para los participantes vivos que se enfrentaron a ``defeated``."""
        if not self.state.award_experience:
            return
        keys = dict(self.state.participants).get(defeated.key, ())
        team = self.state.player.team
        winners = [index for index, m in enumerate(team) if m.key in keys and not m.fainted]
        if not winners:
            return
        amount = experience_yield(
            defeated.base_experience,
            defeated.level,
            len(winners),
            trainer=self.state.kind is BattleKind.TRAINER,
        )
        for index in winners:
            self.gain_experience(index, amount)

    def gain_experience(self, index: int, amount: int) -> None:
        member = self.state.player.team[index]
        if member.level >= MAX_LEVEL:
            return
        ceiling = experience_for_level(member.growth_rate, MAX_LEVEL)
        gained = max(0, min(amount, ceiling - member.experience))
        if not gained:
            return
        self.emit(ExperienceGained(key=member.key, name=member.name, amount=gained))
        member = replace(member, experience=member.experience + gained)
        target = min(MAX_LEVEL, level_for_experience(member.growth_rate, member.experience))
        for level in range(member.level + 1, target + 1):
            member, deltas = level_up(member, level)
            self.emit(LeveledUp(key=member.key, name=member.name, level=level, deltas=deltas))
            member, learned = refresh_auto_moveset(member)
            for move_name, forgot in learned:
                self.emit(
                    MoveLearned(
                        key=member.key, name=member.name, move_name=move_name, forgot=forgot
                    )
                )
        self.state = self.state.with_side("player", self.state.player.replace_member(index, member))

    # --- Usar un movimiento -------------------------------------------------

    def use_move(self, side: SideId, slot: int) -> None:
        user = self.active(side)
        recharging = user.volatile.recharging
        charged = user.volatile.charging
        if recharging:
            self.set_volatile(side, recharging=False)
        move: Move | None = None
        struggling = False
        if recharging:
            move = None
        elif charged is not None:
            move = _charged_move(user, charged)
            if move is None:
                # Sin rastro del golpe (ni en el moveset ni en el learnset), pierde la carga.
                self.set_volatile(side, charging=None, semi_invulnerable=False)
                return
        elif not available_moves(user):
            self.emit(NoMovesLeft(side=side, key=user.key, name=user.name))
            move, struggling = STRUGGLE, True
        else:
            # Se usa el movimiento elegido, no el hueco: si subió de nivel a
            # mitad de turno y su moveset cambió, se busca por slug y, si lo
            # ha olvidado, pierde la acción.
            planned = self.planned.get(side)
            found = slot if planned is None else _slot_of(user, planned)
            if found is None or not 0 <= found < len(user.moves) or user.moves[found].pp <= 0:
                return
            slot = found
            move = user.moves[slot].move
        if charged is not None:
            # El segundo turno descarga el golpe; si no puede moverse, lo pierde.
            self.set_volatile(side, charging=None, semi_invulnerable=False)
        if not self.can_act(side, move) or move is None:
            return
        self.emit(
            MoveUsed(
                side=side,
                key=user.key,
                name=user.name,
                move=move.slug,
                move_name=move.name,
                move_type=move.type,
                damage_class=move.damage_class,
            )
        )
        if charged is None:
            if not struggling:
                self.spend_pp(side, slot)
            self.set_volatile(side, last_move=move.slug)
            if not is_supported(move):
                self.fail(side, FailReason.UNSUPPORTED)
                return
            # Primer turno en el campo ≈ aún no ha usado ningún movimiento desde
            # que entró (``user`` es el de antes de anotar ``last_move``).
            if move.slug in FIRST_TURN_ONLY and user.volatile.last_move is not None:
                self.fail(side, FailReason.GENERIC)
                return
            if move.slug in CHARGE_MOVES:
                self.set_volatile(
                    side, charging=move.slug, semi_invulnerable=move.slug in SEMI_INVULNERABLE
                )
                self.emit(
                    Charging(
                        side=side,
                        key=user.key,
                        name=user.name,
                        move=move.slug,
                        move_name=move.name,
                    )
                )
                return
        self.execute(side, move)

    def spend_pp(self, side: SideId, slot: int) -> None:
        c = self.active(side)
        slots = list(c.moves)
        slots[slot] = replace(slots[slot], pp=max(0, slots[slot].pp - 1))
        self.put(side, replace(c, moves=tuple(slots)))

    def can_act(self, side: SideId, move: Move | None) -> bool:
        """Comprobaciones de Gen 3 antes de moverse; ``move`` es None si recarga.

        Orden: sueño, congelación, recarga, retroceso, confusión y parálisis.
        """
        c = self.active(side)
        if c.status is Status.SLEEP:
            if c.status_turns > 0:
                self.put(side, replace(c, status_turns=c.status_turns - 1))
                self.immobile(side, ImmobileReason.ASLEEP)
                return False
            self.cure(side)
        elif c.status is Status.FREEZE:
            # Un movimiento de Fuego del propio congelado lo descongela.
            if (move is None or move.type != "fire") and self.rng.random() >= THAW_CHANCE:
                self.immobile(side, ImmobileReason.FROZEN)
                return False
            self.cure(side)
        if move is None:
            self.immobile(side, ImmobileReason.RECHARGING)
            return False
        c = self.active(side)
        if c.volatile.flinched:
            self.immobile(side, ImmobileReason.FLINCHED)
            return False
        if c.volatile.confusion_turns > 0 and not self.confusion_check(side):
            return False
        if c.status is Status.PARALYSIS and self.rng.random() < PARALYSIS_CHANCE:
            self.immobile(side, ImmobileReason.PARALYZED)
            return False
        return True

    def immobile(self, side: SideId, reason: ImmobileReason) -> None:
        c = self.active(side)
        self.emit(Immobilized(side=side, key=c.key, name=c.name, reason=reason))

    def confusion_check(self, side: SideId) -> bool:
        """Descuenta la confusión; True si puede actuar, False si se hiere."""
        c = self.active(side)
        left = c.volatile.confusion_turns - 1
        self.set_volatile(side, confusion_turns=max(0, left))
        if left <= 0:
            self.emit(ConfusionEnded(side=side, key=c.key, name=c.name))
            return True
        self.emit(ConfusionCheck(side=side, key=c.key, name=c.name))
        if self.rng.random() >= CONFUSION_HURT_CHANCE:
            return True
        self.emit(ConfusionHurt(side=side, key=c.key, name=c.name))
        self.damage(side, confusion_damage(self.active(side)), DamageCause.CONFUSION)
        return False

    def execute(self, side: SideId, move: Move) -> None:
        if _targets_foe(move) and self.active(other_side(side)).fainted:
            # Sin objetivo (se debilitó antes en este turno): «¡Pero falló!».
            self.fail(side, FailReason.GENERIC)
            return
        if move.is_damaging:
            self.damaging_move(side, move)
            return
        slug = move.slug
        if slug in PROTECT_MOVES:
            self.protect(side)
        elif slug == REST:
            self.rest(side)
        elif slug in SCREENS:
            self.raise_screen(side, SCREENS[slug])
        elif slug in HAZE:
            self.haze()
        elif slug in FOCUS_ENERGY:
            self.focus_energy(side)
        elif slug in SPLASH:
            self.fail(side, FailReason.NOTHING_HAPPENED)
        elif slug in TELEPORT:
            self.teleport(side)
        elif slug in FORCE_SWITCH or move.category == "force-switch":
            if self.lands(side, move):
                self.force_out(other_side(side), primary=True)
        elif move.category == "ailment":
            self.ailment_move(side, move)
        elif move.category == "net-good-stats":
            self.stat_move(side, move)
        elif move.category == "heal":
            self.heal_move(side, move)
        elif move.category == "swagger":
            if self.lands(side, move):
                self.change_stats(other_side(side), move.stat_changes, primary=True)
                self.confuse(other_side(side), primary=True)
        else:
            self.fail(side, FailReason.UNSUPPORTED)

    def lands(
        self, side: SideId, move: Move, *, immune: bool = False, level_gap: bool = False
    ) -> bool:
        """¿Llega al rival? Protección, semi-invulnerable, inmunidad, nivel y precisión."""
        foe_side = other_side(side)
        user, foe = self.active(side), self.active(foe_side)
        if foe.volatile.protected:
            self.emit(Protected(side=foe_side, key=foe.key, name=foe.name))
            return False
        if foe.volatile.semi_invulnerable:
            self.emit(MoveMissed(side=side, key=user.key, name=user.name))
            return False
        if immune:
            self.emit(NoEffect(side=foe_side, key=foe.key, name=foe.name))
            return False
        if level_gap:
            self.fail(side, FailReason.LEVEL_GAP)
            return False
        if not roll_hits(user, foe, move, self.rng):
            self.emit(MoveMissed(side=side, key=user.key, name=user.name))
            return False
        return True

    # --- Movimientos de daño ------------------------------------------------

    def damaging_move(self, side: SideId, move: Move) -> None:
        foe_side = other_side(side)
        user, foe = self.active(side), self.active(foe_side)
        if move.slug in DREAM_EATER and foe.status is not Status.SLEEP:
            self.fail(side, FailReason.TARGET_AWAKE)
            return
        if move.slug in SACRIFICE:
            # Gen 3: el usuario se debilita aunque falle o no afecte.
            self.damage(side, user.hp, DamageCause.SACRIFICE)
        multiplier = move_effectiveness(move, foe.types)
        ohko = move.category == "ohko"
        level_gap = ohko and foe.level > user.level
        if not self.lands(side, move, immune=multiplier == 0, level_gap=level_gap):
            return
        if ohko:
            self.damage(foe_side, foe.hp, DamageCause.MOVE)
            self.emit(OneHitKO(side=foe_side, key=foe.key, name=foe.name))
            return
        total = self.strike(side, move, multiplier)
        self.after_strike(side, move, total)

    def strike(self, side: SideId, move: Move, multiplier: float) -> int:
        """Los golpes del movimiento; devuelve el daño total causado."""
        foe_side = other_side(side)
        fixed = is_fixed_damage(move)
        hits = roll_hit_count(move, self.rng)
        total = landed = 0
        for _ in range(hits):
            user, foe = self.active(side), self.active(foe_side)
            if foe.fainted:
                break
            critical = not fixed and roll_critical(user, move, self.rng)
            amount = self.hit_damage(user, foe, foe_side, move, critical)
            if move.slug in FALSE_SWIPE:
                amount = min(amount, foe.hp - 1)
            dealt = self.damage(
                foe_side,
                amount,
                DamageCause.MOVE,
                effectiveness=1.0 if fixed else multiplier,
                critical=critical,
            )
            total += dealt
            landed += 1
            target = self.active(foe_side)
            if move.type == "fire" and not target.fainted and target.status is Status.FREEZE:
                self.cure(foe_side)
        if hit_range(move) != (1, 1):
            foe = self.active(foe_side)
            self.emit(HitCount(side=foe_side, key=foe.key, name=foe.name, hits=landed))
        return total

    def hit_damage(
        self, user: Combatant, foe: Combatant, foe_side: SideId, move: Move, critical: bool
    ) -> int:
        fixed = fixed_damage(user, foe, move)
        if fixed is not None:
            return fixed
        roll = self.rng.randint(MIN_ROLL, MAX_ROLL)
        screens = self.state.side(foe_side)
        if move.damage_class is DamageClass.PHYSICAL:
            screen = screens.reflect_turns > 0
        else:
            screen = screens.light_screen_turns > 0
        return calculate_damage(user, foe, move, critical=critical, roll=roll, screen=screen)

    def after_strike(self, side: SideId, move: Move, total: int) -> None:
        """Drenaje, efectos secundarios, retroceso, recarga y relevo forzado."""
        foe_side = other_side(side)
        if move.drain > 0 and total > 0:
            self.heal(side, max(1, total * move.drain // 100), HealCause.DRAIN)
        if not self.active(foe_side).fainted:
            self.secondary_effects(side, move)
        if (
            move.category == "damage-raise"
            and move.stat_changes
            and not self.active(side).fainted
            and self.chance(move.stat_chance, zero_is_certain=True)
        ):
            self.change_stats(side, move.stat_changes, primary=False)
        if move.drain < 0 and total > 0 and not self.active(side).fainted:
            self.damage(side, max(1, total * -move.drain // 100), DamageCause.RECOIL)
        if move.healing < 0 and total > 0 and not self.active(side).fainted:
            # Forcejeo: el usuario pierde un porcentaje de sus PS máximos.
            max_hp = self.active(side).max_hp
            self.damage(side, max(1, max_hp * -move.healing // 100), DamageCause.RECOIL)
        if move.slug in RECHARGE_MOVES and not self.active(side).fainted:
            self.set_volatile(side, recharging=True)
        if (
            move.category == "force-switch"
            and not self.active(foe_side).fainted
            and not self.active(side).fainted
        ):
            self.force_out(foe_side, primary=False)

    def secondary_effects(self, side: SideId, move: Move) -> None:
        """Problemas, bajadas y retroceso sobre un rival que sigue en pie."""
        foe_side = other_side(side)
        ailment = move.ailment
        if ailment in _AILMENT_STATUS or ailment in _VOLATILE_AILMENTS:
            if self.chance(move.ailment_chance, zero_is_certain=False):
                self.apply_ailment(foe_side, move, primary=False)
        if (
            move.category == "damage-lower"
            and move.stat_changes
            and self.chance(move.stat_chance, zero_is_certain=True)
        ):
            self.change_stats(foe_side, move.stat_changes, primary=False)
        # Retroceso: solo si el objetivo aún no se ha movido en este turno.
        if (
            move.flinch_chance > 0
            and foe_side not in self.acted
            and not self.active(foe_side).fainted
            and self.chance(move.flinch_chance, zero_is_certain=False)
        ):
            self.set_volatile(foe_side, flinched=True)

    # --- Problemas de estado y cambios de características --------------------

    def apply_ailment(self, target_side: SideId, move: Move, *, primary: bool) -> None:
        ailment = move.ailment
        if ailment == "confusion":
            self.confuse(target_side, primary=primary)
        elif ailment == "trap":
            self.trap(target_side, move)
        elif ailment == "leech-seed":
            self.seed(target_side, primary=primary)
        else:
            status = Status.TOXIC if move.slug in TOXIC_MOVES else _AILMENT_STATUS[ailment]
            self.inflict(target_side, status, primary=primary)

    def inflict(self, side: SideId, status: Status, *, primary: bool) -> None:
        c = self.active(side)
        if not can_receive_status(c, status):
            if primary:
                already = c.status is not Status.NONE
                self.emit(
                    StatusBlocked(side=side, key=c.key, name=c.name, status=status, already=already)
                )
            return
        turns = 0
        if status is Status.SLEEP:
            turns = self.rng.randint(*SLEEP_TURNS)
        elif status is Status.TOXIC:
            turns = 1
        self.put(side, replace(c, status=status, status_turns=turns))
        self.emit(StatusInflicted(side=side, key=c.key, name=c.name, status=status))

    def confuse(self, side: SideId, *, primary: bool) -> None:
        c = self.active(side)
        if c.fainted:
            return
        if c.volatile.confusion_turns > 0:
            if primary:
                self.fail(other_side(side), FailReason.GENERIC)
            return
        self.set_volatile(side, confusion_turns=self.rng.randint(*CONFUSION_TURNS))
        self.emit(Confused(side=side, key=c.key, name=c.name))

    def seed(self, side: SideId, *, primary: bool) -> None:
        c = self.active(side)
        if c.volatile.seeded or "grass" in c.types:
            if primary:
                self.fail(other_side(side), FailReason.GENERIC)
            return
        self.set_volatile(side, seeded=True)
        self.emit(Seeded(side=side, key=c.key, name=c.name))

    def trap(self, side: SideId, move: Move) -> None:
        c = self.active(side)
        if c.volatile.trapped_turns > 0:
            return
        low, high = _trap_turns(move)
        turns = low if low == high else self.rng.randint(low, high)
        self.set_volatile(side, trapped_turns=turns, trapped_by=move.name)
        self.emit(Trapped(side=side, key=c.key, name=c.name, move_name=move.name))

    def change_stats(self, side: SideId, changes: Sequence[StatChange], *, primary: bool) -> None:
        """Aplica escalones acotados a ±6; los secundarios topados no se anuncian."""
        for change in changes:
            c = self.active(side)
            if c.fainted or change.stat not in STAGE_KEYS:
                continue
            stages, applied = c.stages.shifted(change.stat, change.change)
            if applied:
                self.put(side, replace(c, stages=stages))
                self.emit(
                    StatChanged(side=side, key=c.key, name=c.name, stat=change.stat, delta=applied)
                )
            elif primary:
                rising = change.change > 0
                self.emit(
                    StatUnchanged(
                        side=side, key=c.key, name=c.name, stat=change.stat, rising=rising
                    )
                )

    # --- Movimientos de estado ----------------------------------------------

    def ailment_move(self, side: SideId, move: Move) -> None:
        ailment = move.ailment
        foe = self.active(other_side(side))
        # Mal de Ojo y compañía (atrapar sin daño) no tienen efecto en el motor.
        if ailment == "trap" or not (ailment in _AILMENT_STATUS or ailment in _VOLATILE_AILMENTS):
            self.fail(side, FailReason.UNSUPPORTED)
            return
        # Planta es inmune a Drenadoras; en Gen 3, los movimientos que
        # paralizan respetan la tabla de tipos (Onda Trueno no afecta a Tierra).
        immune = (ailment == "leech-seed" and "grass" in foe.types) or (
            ailment == "paralysis" and move_effectiveness(move, foe.types) == 0
        )
        if not self.lands(side, move, immune=immune):
            return
        if not self.chance(move.ailment_chance, zero_is_certain=True):
            self.fail(side, FailReason.GENERIC)
            return
        self.apply_ailment(other_side(side), move, primary=True)

    def stat_move(self, side: SideId, move: Move) -> None:
        if move.target == "ally":
            # En un combate individual no hay aliado al que ayudar.
            self.fail(side, FailReason.GENERIC)
            return
        on_user = move.target in SELF_TARGETS
        if not on_user and not self.lands(side, move):
            return
        if not self.chance(move.stat_chance, zero_is_certain=True):
            self.fail(side, FailReason.GENERIC)
            return
        recipient = side if on_user else other_side(side)
        self.change_stats(recipient, move.stat_changes, primary=True)

    def heal_move(self, side: SideId, move: Move) -> None:
        if move.target == "ally":
            self.fail(side, FailReason.GENERIC)
            return
        on_user = move.target in SELF_TARGETS
        if not on_user and not self.lands(side, move):
            return
        # Pulso Cura y similares curan a su objetivo: en individual, el rival.
        recipient = side if on_user else other_side(side)
        target = self.active(recipient)
        amount = target.max_hp * move.healing // 100
        if target.hp >= target.max_hp or amount <= 0:
            self.fail(side, FailReason.GENERIC)
            return
        self.heal(recipient, max(1, amount), HealCause.MOVE)

    def protect(self, side: SideId) -> None:
        user = self.active(side)
        streak = user.volatile.protect_streak
        # Falla si ya no queda nadie por moverse o, encadenada, con 1/2, 1/4, 1/8.
        if other_side(side) in self.acted or (
            streak > 0 and self.rng.random() >= 0.5 ** min(streak, PROTECT_CHAIN_LIMIT)
        ):
            self.fail(side, FailReason.GENERIC)
            return
        self.set_volatile(side, protected=True, protect_streak=streak + 1)
        self.emit(ProtectionRaised(side=side, key=user.key, name=user.name))

    def rest(self, side: SideId) -> None:
        user = self.active(side)
        if user.hp >= user.max_hp:
            self.fail(side, FailReason.GENERIC)
            return
        self.heal(side, user.max_hp - user.hp, HealCause.REST)
        rested = replace(self.active(side), status=Status.SLEEP, status_turns=REST_SLEEP_TURNS)
        self.put(side, rested)
        self.emit(StatusInflicted(side=side, key=user.key, name=user.name, status=Status.SLEEP))

    def raise_screen(self, side: SideId, screen: str) -> None:
        own = self.state.side(side)
        reflect = screen == "reflect"
        if (own.reflect_turns if reflect else own.light_screen_turns) > 0:
            self.fail(side, FailReason.GENERIC)
            return
        if reflect:
            own = replace(own, reflect_turns=SCREEN_TURNS)
        else:
            own = replace(own, light_screen_turns=SCREEN_TURNS)
        self.state = self.state.with_side(side, own)
        self.emit(ScreenRaised(side=side, screen=screen))

    def haze(self) -> None:
        for side in SIDES:
            c = self.active(side)
            if c.stages != Stages():
                self.put(side, replace(c, stages=Stages()))
        self.emit(StatsReset())

    def focus_energy(self, side: SideId) -> None:
        user = self.active(side)
        if user.volatile.focus_energy:
            self.fail(side, FailReason.GENERIC)
            return
        self.set_volatile(side, focus_energy=True)
        self.emit(PumpedUp(side=side, key=user.key, name=user.name))

    def teleport(self, side: SideId) -> None:
        if self.state.kind is not BattleKind.WILD:
            self.fail(side, FailReason.GENERIC)
            return
        user = self.active(side)
        self.emit(Teleported(side=side, key=user.key, name=user.name))
        self.end(Outcome.ESCAPED if side == "player" else Outcome.OPPONENT_FLED)

    def force_out(self, side: SideId, *, primary: bool) -> None:
        """Rugido y compañía: en salvaje acaba el combate; si no, relevo al azar."""
        target = self.active(side)
        if self.state.kind is BattleKind.WILD:
            self.emit(ForcedOut(side=side, key=target.key, name=target.name))
            self.end(Outcome.OPPONENT_FLED if side == "opponent" else Outcome.ESCAPED)
            return
        choices = switch_targets(self.state, side)
        if not choices:
            if primary:
                self.fail(other_side(side), FailReason.GENERIC)
            return
        pick = 0 if len(choices) == 1 else self.rng.randint(0, len(choices) - 1)
        self.emit(ForcedOut(side=side, key=target.key, name=target.name))
        self.withdraw(side)
        self.send_out(side, choices[pick])

    # --- Fin de turno -------------------------------------------------------

    def end_of_turn(self, order: tuple[SideId, SideId]) -> None:
        for side in order:
            self.residual(side)
            if self.finished:
                return
        self.fade_screens()
        self.clear_turn_flags()

    def residual(self, side: SideId) -> None:
        """Quemadura o veneno, Drenadoras y atrapado, por ese orden."""
        if self.active(side).fainted:
            return
        self.status_damage(side)
        if self.residual_stops(side):
            return
        self.leech_seed(side)
        if self.residual_stops(side):
            return
        self.trap_damage(side)
        self.residual_stops(side)

    def residual_stops(self, side: SideId) -> bool:
        """Anuncia un desmayo residual; True si ``side`` no sigue o acabó el combate."""
        self.settle(side)
        self.check_end()
        return self.finished or self.active(side).fainted

    def status_damage(self, side: SideId) -> None:
        c = self.active(side)
        if c.status is Status.BURN:
            self.damage(side, max(1, c.max_hp // BURN_DIVISOR), DamageCause.BURN)
        elif c.status is Status.POISON:
            self.damage(side, max(1, c.max_hp // POISON_DIVISOR), DamageCause.POISON)
        elif c.status is Status.TOXIC:
            counter = max(1, c.status_turns)
            self.damage(side, max(1, c.max_hp * counter // TOXIC_DIVISOR), DamageCause.POISON)
            counted = replace(self.active(side), status_turns=min(counter + 1, TOXIC_MAX_COUNTER))
            self.put(side, counted)

    def leech_seed(self, side: SideId) -> None:
        c = self.active(side)
        foe_side = other_side(side)
        if not c.volatile.seeded or self.active(foe_side).fainted:
            return
        drained = self.damage(side, max(1, c.max_hp // LEECH_SEED_DIVISOR), DamageCause.LEECH_SEED)
        self.heal(foe_side, drained, HealCause.LEECH_SEED)

    def trap_damage(self, side: SideId) -> None:
        c = self.active(side)
        if c.volatile.trapped_turns <= 0:
            return
        move_name = c.volatile.trapped_by or ""
        self.damage(side, max(1, c.max_hp // TRAP_DIVISOR), DamageCause.TRAP)
        left = c.volatile.trapped_turns - 1
        self.set_volatile(side, trapped_turns=left, trapped_by=move_name if left else None)
        if not left and not self.active(side).fainted:
            self.emit(Released(side=side, key=c.key, name=c.name, move_name=move_name))

    def fade_screens(self) -> None:
        for side in SIDES:
            own = self.state.side(side)
            reflect = max(0, own.reflect_turns - 1)
            light = max(0, own.light_screen_turns - 1)
            if (reflect, light) == (own.reflect_turns, own.light_screen_turns):
                continue
            self.state = self.state.with_side(
                side, replace(own, reflect_turns=reflect, light_screen_turns=light)
            )
            if own.reflect_turns and not reflect:
                self.emit(ScreenFaded(side=side, screen="reflect"))
            if own.light_screen_turns and not light:
                self.emit(ScreenFaded(side=side, screen="light-screen"))

    def clear_turn_flags(self) -> None:
        """Retroceso y Protección duran un turno; la cadena se rompe si no se protegió."""
        for side in SIDES:
            c = self.active(side)
            volatile = c.volatile
            streak = volatile.protect_streak if volatile.protected else 0
            if volatile.flinched or volatile.protected or streak != volatile.protect_streak:
                cleared = replace(volatile, flinched=False, protected=False, protect_streak=streak)
                self.put(side, replace(c, volatile=cleared))
