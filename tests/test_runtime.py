import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE = ROOT / 'src' / 'pro1x_nordic.py'


def load():
    if not MODULE.exists():
        return None
    spec = importlib.util.spec_from_file_location('pro1x_nordic', MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MapTests(unittest.TestCase):
    def test_activation_preserves_forced_shift_capability(self):
        m = load()
        self.assertIsNotNone(m, 'Missing safe modifier-map implementation')
        state = list(m.ORIGINAL)
        supported = set(state)
        def put(index, value):
            old = state[index]
            state[index] = value
            supported.discard(old)
            supported.add(value)
            if old in state:
                supported.add(old)
            self.assertIn(54, supported)
        m.transition(lambda:list(state), put, True)
        self.assertEqual(state, list(m.NORDIC))
        m.transition(lambda:list(state), put, False)
        self.assertEqual(state, list(m.ORIGINAL))
    def test_io_failure_restores_entry_state(self):
        m = load()
        assert m is not None
        for enable in (True, False):
            initial = m.ORIGINAL if enable else m.NORDIC
            for fail_at in range(3):
                with self.subTest(enable=enable, fail_at=fail_at):
                    state=list(initial)
                    count=[0]
                    def put(index,value):
                        count[0]+=1
                        if count[0]==fail_at+1:
                            raise OSError('injected ioctl failure')
                        state[index]=value
                    with self.assertRaises(OSError):
                        m.transition(lambda:list(state),put,enable)
                    self.assertEqual(state,list(initial))

    def test_recovers_only_package_produced_intermediate_states(self):
        m=load()
        assert m is not None
        self.assertTrue(hasattr(m,'recover'),'Missing partial-transaction recovery')
        states = [m.ORIGINAL, m.NORDIC,
                  (56,54,29,125,464,54),
                  (56,42,29,125,464,54),
                  (56,54,29,125,100,54)]
        for before in states:
            state=list(before)
            supported=set(state)
            def put(index,value):
                old=state[index]; state[index]=value
                supported.discard(old); supported.add(value)
                if old in state:supported.add(old)
                self.assertIn(54,supported)
            m.recover(lambda:list(state),put)
            self.assertEqual(state,list(m.ORIGINAL))
        writes=[]
        with self.assertRaises(m.UnsafeState):
            m.recover(lambda:[56,42,29,125,464,464],lambda *x:writes.append(x))
        self.assertEqual(writes,[])

    def test_unknown_maps_are_not_modified(self):
        m=load()
        assert m is not None
        writes=[]
        with self.assertRaises(m.UnsafeState):
            m.transition(lambda:[56,99,29,125,464,464],lambda *a:writes.append(a),True)
        self.assertEqual(writes,[])


if __name__ == '__main__':
    unittest.main()
