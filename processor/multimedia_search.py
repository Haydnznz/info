def run(sources):
    return [s for s in sources if s.get('input_family') == 'multimedia']
