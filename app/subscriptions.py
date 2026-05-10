from sqlalchemy import select
from sqlalchemy.orm import Session

from app.context import DEFAULT_PROFILE_ID
from app.models import Source, SourceSubscription
from app.utils import dumps, loads


def get_subscription(db: Session, source_id: str, profile_id: str = DEFAULT_PROFILE_ID) -> SourceSubscription | None:
    return db.get(SourceSubscription, (profile_id, source_id))


def subscribed_source_ids(db: Session, profile_id: str = DEFAULT_PROFILE_ID) -> list[str]:
    return list(
        db.execute(
            select(SourceSubscription.source_id)
            .join(Source, Source.id == SourceSubscription.source_id)
            .where(SourceSubscription.profile_id == profile_id, SourceSubscription.subscribed.is_(True))
            .order_by(Source.group, Source.priority, Source.name)
        ).scalars()
    )


def is_subscribed(db: Session, source_id: str, profile_id: str = DEFAULT_PROFILE_ID) -> bool:
    subscription = get_subscription(db, source_id, profile_id)
    return bool(subscription and subscription.subscribed)


def subscribe_source(db: Session, source_id: str, profile_id: str = DEFAULT_PROFILE_ID) -> SourceSubscription:
    if not db.get(Source, source_id):
        raise KeyError(source_id)
    subscription = get_subscription(db, source_id, profile_id)
    if not subscription:
        subscription = SourceSubscription(source_id=source_id, profile_id=profile_id, subscribed=True)
        db.add(subscription)
    else:
        subscription.subscribed = True
    _expire_profile_recommendations(db, profile_id)
    db.commit()
    db.refresh(subscription)
    return subscription


def unsubscribe_source(db: Session, source_id: str, profile_id: str = DEFAULT_PROFILE_ID) -> SourceSubscription:
    if not db.get(Source, source_id):
        raise KeyError(source_id)
    subscription = get_subscription(db, source_id, profile_id)
    if not subscription:
        subscription = SourceSubscription(source_id=source_id, profile_id=profile_id, subscribed=False)
        db.add(subscription)
    else:
        subscription.subscribed = False
    _expire_profile_recommendations(db, profile_id)
    db.commit()
    db.refresh(subscription)
    return subscription


def subscription_to_dict(subscription: SourceSubscription) -> dict:
    return {
        "source_id": subscription.source_id,
        "profile_id": subscription.profile_id,
        "subscribed": subscription.subscribed,
        "priority_override": subscription.priority_override,
        "settings_override": loads(subscription.settings_override, {}),
    }


def update_subscription_settings(
    db: Session,
    source_id: str,
    settings_override: dict,
    priority_override: int | None = None,
    profile_id: str = DEFAULT_PROFILE_ID,
) -> SourceSubscription:
    if not db.get(Source, source_id):
        raise KeyError(source_id)
    subscription = get_subscription(db, source_id, profile_id)
    if not subscription:
        subscription = SourceSubscription(source_id=source_id, profile_id=profile_id, subscribed=True)
        db.add(subscription)
    subscription.settings_override = dumps(settings_override)
    subscription.priority_override = priority_override
    _expire_profile_recommendations(db, profile_id)
    db.commit()
    db.refresh(subscription)
    return subscription


def _expire_profile_recommendations(db: Session, profile_id: str) -> None:
    from app.services.core import _expire_recommendation_scores

    _expire_recommendation_scores(db, profile_id=profile_id)
