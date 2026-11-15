def button(text, data=None, url=None):
    return {'text': text, **({'url': url} if url else {'callback_data': data})}


def keyboard(*rows):
    return {'inline_keyboard': list(rows)}
