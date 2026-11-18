"""One role policy for both visible controls and server-side validation."""
ROLE_LABELS={'unset':'À configurer','portal':'Portail','chat':'Chat classique','always':'Chat 24 h/24',
             'vip':'VIP','proof':'Preuve','free':'Gratuit'}
ROLE_FIELDS={
    'unset':frozenset(),
    'portal':frozenset({'description','ads'}),
    'chat':frozenset({'title','description','goal','prices','locker','threshold','slot','rules','anti','ads'}),
    'always':frozenset({'title','description','goal','prices','locker','threshold','rules','anti','ads'}),
    'vip':frozenset({'title','description','prices','monthly','locker','threshold','rules','anti','ads'}),
    'proof':frozenset({'title','description','rules'}),
    'free':frozenset({'title','description','locker','threshold','slot','weekday','rules','anti','ads'}),
}
WEEKDAYS=('Lundi','Mardi','Mercredi','Jeudi','Vendredi','Samedi','Dimanche')


def allows(role,field):
    return field in ROLE_FIELDS.get(role,frozenset())
