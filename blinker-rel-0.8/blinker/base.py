# -*- coding: utf-8; fill-column: 76 -*-
"""Signals and events.

A small implementation of signals, inspired by a snippet of Django signal
API client code seen in a blog post.  Signals are first-class objects and
each manages its own receivers and message emission.

The :func:`signal` function provides singleton behavior for named signals.

"""
from weakref import WeakValueDictionary

from blinker._utilities import (
    WeakTypes,
    defaultdict,
    hashable_identity,
    reference,
    symbol,
    )


ANY = symbol('ANY')
ANY_ID = 0


class Signal(object):
    """A generic notification emitter."""

    #: A convenience for importers, allows Signal.ANY
    ANY = ANY

    def __init__(self, doc=None):
        if doc:
            self.__doc__ = doc
        self.receivers = {}
        self._by_receiver = defaultdict(set)
        self._by_sender = defaultdict(set)
        self._weak_senders = {}

    def connect(self, receiver, sender=ANY, weak=True):
        """Connect *receiver* to signal events send by *sender*.

        :param receiver: A callable.  Will be invoked by :meth:`send`.  Will
          be invoked with `sender=` as a named argument and any \*\*kwargs
          that were provided to a call to :meth:`send`.

        :param sender: Any object or :attr:`Signal.ANY`.  Restricts
          notifications to *receiver* to only those :meth:`send` emissions
          sent by *sender*.  If ``ANY``, the receiver will always be
          notified.  A *receiver* may be connected to multiple *sender* on
          the same Signal.  Defaults to ``ANY``.

        :param weak: If true, the Signal will hold a weakref to *receiver*
          and automatically disconnect when *receiver* goes out of scope or
          is garbage collected.  Defaults to True.

        """
        receiver_id = hashable_identity(receiver)
        if weak:
            receiver_ref = reference(receiver, self._cleanup_receiver)
            receiver_ref.receiver_id = receiver_id
        else:
            receiver_ref = receiver
        if sender is ANY:
            sender_id = ANY_ID
        else:
            sender_id = hashable_identity(sender)
        # 主要需要登记 receiver_id, sender_id， receiver_ref
        # receiver_ref 对应两种情况 1. 当要求存入弱引用时(选项weak 为 true，需要对传入的引用做一次弱引用的封装)
        #                          2. 选项为false时，直接赋值为原值就好了
        # receiver的引用保存在 receivers dict 中，通过receiver_id进行查询

        self.receivers.setdefault(receiver_id, receiver_ref)
        # _by_sender 字典用来保存对应于每个sender的receiver 订阅者
        # _by_receiver 字典相反，用来保存对于每个 receiver 订阅者的 sender
        self._by_sender[sender_id].add(receiver_id)
        self._by_receiver[receiver_id].add(sender_id)
        # todo 这个del 很奇怪，每次函数结束receiver_ref是会被自动删除的才对，为什么要自行del
        del receiver_ref

        if sender is not ANY and sender_id not in self._weak_senders:
            # wire together a cleanup for weakref-able senders
            try:
                sender_ref = reference(sender, self._cleanup_sender)
                sender_ref.sender_id = sender_id
            except TypeError:
                pass
            # 第一次碰到 try-except中的else。这个else对应于 没有exception抛出的情况 要执行的内容
            else:
                self._weak_senders.setdefault(sender_id, sender_ref)
                del sender_ref

        # broadcast this connection.  if receivers raise, disconnect.
        # todo receiver_connected 的作用？
        # 每次对任意一个Signal（当然receriver_connected Signal除外）,
        #  都会触发receiver_connected信号，我们可以对receiver_connected 信号进行订阅，做一些有用的事情，比如记录每次信号触发的信息
        if receiver_connected.receivers and self is not receiver_connected:
            # 判断 self is not receiver_connected 十分重要，不然会引起死循环
            try:
                receiver_connected.send(self,   # sender 是Signal自己
                                        receiver_arg=receiver,     # 下面的args 是记录的信号收发的信息，三要素：收信人，寄信人，是否要求弱引用
                                        sender_arg=sender,
                                        weak_arg=weak)
            except:
                self.disconnect(receiver, sender)
                raise
        return receiver

    # 为了区别单个sender 每个 receiver 的回应，send()函数返回的是(receiver, receiver(**kwargs) ) 元组的列表
    def send(self, *sender, **kwargs):
        """Emit this signal on behalf of *sender*, passing on \*\*kwargs.

        Returns a list of 2-tuples, pairing receivers with their return
        value. The ordering of receiver notification is undefined.

        :param \*sender: Any object or ``None``.  If omitted, synonymous
        with ``None``.  Only accepts one positional argument.

        :param \*\*kwargs: Data to be sent to receivers.

        """
        # Using '*sender' rather than 'sender=None' allows 'sender' to be
        # used as a keyword argument- i.e. it's an invisible name in the
        # function signature.
        if len(sender) == 0:
            sender = None
        elif len(sender) > 1:
            raise TypeError('send() accepts only one positional argument, '
                            '%s given' % len(sender))
        else:
            sender = sender[0]
        if not self.receivers:
            return []
        else:
            return [(receiver, receiver(sender, **kwargs))
                    for receiver in self.receivers_for(sender)]

    def has_receivers_for(self, sender):
        """True if there is probably a receiver for *sender*.

        Performs an optimistic check for receivers only.  Does not guarantee
        that all weakly referenced receivers are still alive.  See
        :meth:`receivers_for` for a stronger search.

        """
        if not self.receivers:
            return False
        if self._by_sender[ANY_ID]:
            return True
        if sender is ANY:
            return False
        return hashable_identity(sender) in self._by_sender

    # 行为像一个 property 函数，对字典 _by_senders 进行了包装
    def receivers_for(self, sender):
        """Iterate all live receivers listening for *sender*."""
        # TODO: test receivers_for(ANY)
        if self.receivers:
            sender_id = hashable_identity(sender)
            if sender_id in self._by_sender:
                ids = (self._by_sender[ANY_ID] |    # _by_sender 映射 senderid -> receiverids
                       self._by_sender[sender_id])  # question : 这里对两个 id set做或操作意图是？
                                                    # 每个sender 应该对应的 receivers 不应该只是明确订阅是自己的 receiver
                                                    # 还应该包括 对应 ANY 的receivers，所以这里对 set 做了 或操作
                                                    # ps: {1,2,3} | {2,3,4} == {1,2,3,4}
            else:  # 对应 sender_id 不存在 _by_sender 中的情况。这种情况 可能 对应用户自己输错 sender todo
                ids = self._by_sender[ANY_ID].copy()
            for receiver_id in ids:
                receiver = self.receivers.get(receiver_id)
                if receiver is None:
                    continue
                if isinstance(receiver, WeakTypes):
                    strong = receiver()
                    if strong is None:
                        self._disconnect(receiver_id, ANY_ID)
                        continue
                    receiver = strong
                yield receiver
                # 返回的是 正常的 强引用 类型

    def disconnect(self, receiver, sender=ANY):
        """Disconnect *receiver* from this signal's events."""
        if sender is ANY:
            sender_id = ANY_ID
        else:
            sender_id = hashable_identity(sender)
        receiver_id = hashable_identity(receiver)
        self._disconnect(receiver_id, sender_id)

    def _disconnect(self, receiver_id, sender_id):
        if sender_id == ANY_ID:
            if self._by_receiver.pop(receiver_id, False):
                for bucket in self._by_sender.values():
                    # 当 sender_id 为 ANY_ID 时，有必要检查每个 sender 对应的 receivers，并从 receivers 中尝试删去 receiver_id
                    bucket.discard(receiver_id)
            self.receivers.pop(receiver_id, None)
        else:
            self._by_sender[sender_id].discard(receiver_id)

    def _cleanup_receiver(self, receiver_ref):
        """Disconnect a receiver from all senders."""
        self._disconnect(receiver_ref.receiver_id, ANY_ID)

    def _cleanup_sender(self, sender_ref):
        """Disconnect all receivers from a sender."""
        sender_id = sender_ref.sender_id
        assert sender_id != ANY_ID
        self._weak_senders.pop(sender_id, None)
        for receiver_id in self._by_sender.pop(sender_id, ()):
            self._by_receiver[receiver_id].discard(sender_id)

    def _clear_state(self):
        """Throw away all signal state.  Useful for unit tests."""
        self._weak_senders.clear()
        self.receivers.clear()
        self._by_sender.clear()
        self._by_receiver.clear()


receiver_connected = Signal()


class NamedSignal(Signal):
    """A named generic notification emitter."""

    def __init__(self, name, doc=None):
        Signal.__init__(self, doc)
        self.name = name

    def __repr__(self):
        base = Signal.__repr__(self)
        return "%s; %r>" % (base[:-1], self.name)


class Namespace(WeakValueDictionary):

    def signal(self, name, doc=None):
        """Return the :class:`NamedSignal` *name*, creating it if required."""
        try:
            return self[name]
        except KeyError:
            return self.setdefault(name, NamedSignal(name, doc))


signal = Namespace().signal
