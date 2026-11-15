from .inline import button
from app.utils.group_roles import ROLE_LABELS, WEEKDAYS, allows


def group_controls(group,owner):
    gid=group['id']; role=group['role']; rows=[]
    if owner: rows.append([button('Rôle : '+ROLE_LABELS.get(role,role),'roles:'+gid)])
    if allows(role,'description'):
        rows.append([button('📝 Texte du portail' if role=='portal' else '📝 Description','edit:'+gid+':description')])
    if role=='portal' and group['enabled']:
        rows.append([button('📢 Publier / actualiser le portail','publish:'+gid)])
    if allows(role,'goal'):
        rows.append([button('🎯 Objectif : '+str(group['goal'])+' invitations','edit:'+gid+':goal')])
    if allows(role,'prices'): rows.append([button('💳 Tarifs','prices:'+gid)])
    if allows(role,'monthly'):
        rows.append([button('Paiement : '+('mensuel' if group['monthly'] else 'unique'),'toggle:'+gid+':monthly')])
    if allows(role,'locker'):
        row=[button('🔒 Locker : '+('ON' if group['locker'] else 'OFF'),'toggle:'+gid+':locker')]
        if group['locker']: row.append(button('Seuil : '+str(group['threshold']),'edit:'+gid+':threshold'))
        rows.append(row)
    if allows(role,'slot'): rows.append([button('🕐 Horaires : '+group['slot'],'slots:'+gid)])
    if allows(role,'weekday'): rows.append([button('📅 Ouverture : '+WEEKDAYS[group['weekday']],'edit:'+gid+':weekday')])
    if allows(role,'rules'): rows.append([button('📜 Règles','edit:'+gid+':rules')])
    if allows(role,'anti'): rows.append([button('Anti-repost : '+('ON' if group['anti'] else 'OFF'),'toggle:'+gid+':anti')])
    if allows(role,'ads'): rows.append([button('📣 Publicités','ads:'+gid)])
    if owner:
        if role!='unset':
            label='⏸ Désactiver' if group['enabled'] else ('✅ Activer et publier' if role=='portal' else '✅ Activer')
            rows.append([button(label,'toggle:'+gid+':enabled')])
        rows.append([button('🔁 Remplacer le groupe Telegram','edit:'+gid+':chat')])
    rows.append([button('🔄 Actualiser','a:'+gid),button('⬅️ Administration','admin' if owner else 'home')])
    return rows
