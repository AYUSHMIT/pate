{-# LANGUAGE DataKinds #-}
{-# LANGUAGE TypeFamilies #-}
{-# LANGUAGE MultiParamTypeClasses #-}
{-# LANGUAGE UndecidableInstances #-}
{-# LANGUAGE OverloadedStrings #-}
{-# LANGUAGE FlexibleContexts #-}


{-# OPTIONS_GHC -fno-warn-orphans #-}

module Pate.Verification.StrongestPosts.CounterExample
  ( TotalityCounterexample(..)
  , ObservableCounterexample(..)
  , RegsCounterExample(..)
  , prettyRegsCE
  ) where


import           Data.Text (Text)
import           Numeric (showHex)

import qualified Data.Macaw.CFG as MM
import qualified Data.Macaw.CFGSlice as MCS

import qualified Prettyprinter as PP
import           Prettyprinter ( (<+>) )

import qualified Pate.Memory.MemTrace as MT
import           Pate.TraceTree
import qualified Pate.Proof.Instances as PPI
import qualified Pate.Solver as PSo
import qualified Pate.SimulatorRegisters as PSR
import qualified Pate.PatchPair as PPa
import Control.Monad.Identity
import Control.Monad.Trans.Writer
import Pate.Register.Traversal (zipWithRegStatesM)
import qualified Pate.Arch as PA

-- | A totality counterexample represents a potential control-flow situation that represents
--   desynchronization of the original and patched program. The first tuple represents
--   the control-flow of the original program, and the second tuple represents the patched
--   program.  Each tuple contains the target address of the control-flow instruction,
--   the type of control-flow it represents, and the address and dissassembly of the
--   instruction causing the control flow. The final node is a Maybe because we cannot
--   entirely guarantee that the information on the instruction causing control flow is
--   avaliable, although we expect that it always should be.
--
--   Note that because of the overapproximation implied by the abstract domains, the
--   computed counterexamples may actually not be realizable.
data TotalityCounterexample ptrW =
  TotalityCounterexample
    (Integer, MCS.MacawBlockEndCase, Maybe (MM.MemSegmentOff ptrW, Text))
    (Integer, MCS.MacawBlockEndCase, Maybe (MM.MemSegmentOff ptrW, Text))


instance MM.MemWidth (MM.RegAddrWidth (MM.ArchReg arch)) => IsTraceNode '(sym,arch) "totalityce" where
  type TraceNodeType '(sym,arch) "totalityce" = TotalityCounterexample (MM.ArchAddrWidth arch)
  prettyNode () (TotalityCounterexample (oIP,oEnd,oInstr) (pIP,pEnd,pInstr)) = PP.vsep $
      ["Found extra exit while checking totality:"
      , PP.pretty (showHex oIP "") <+> PP.pretty (PPI.ppExitCase oEnd) <+> PP.pretty (show oInstr)
      , PP.pretty (showHex pIP "") <+> PP.pretty (PPI.ppExitCase pEnd) <+> PP.pretty (show pInstr)
      ]
  nodeTags = [(Summary, \_ _ -> "Not total") ]

data RegsCounterExample sym arch =
  RegsCounterExample 
    (MM.RegState (MM.ArchReg arch) (PSR.MacawRegEntry sym))
    (MM.RegState (MM.ArchReg arch) (PSR.MacawRegEntry sym))

prettyRegsCE ::
  (PA.ValidArch arch, PSo.ValidSym sym) =>
  RegsCounterExample sym arch -> PP.Doc a
prettyRegsCE (RegsCounterExample rsO rsP) = runIdentity $ do
  ps <- execWriterT $ do
    _ <- zipWithRegStatesM rsO rsP $ \r rO rP -> 
      case rO == rP of
        False | PA.Normal ppreg <- fmap PP.pretty (PA.displayRegister r) -> tell [ppreg] >> return rO
        _ -> return rO
    return ()
  return $ PP.braces $ PP.fillSep $ PP.punctuate "," ps

-- | An observable counterexample consists of a sequence of observable events
--   that differ in some way.  The first sequence is generated by the
--   original program, and the second is from the patched program.
--
--   Note that because of the overapproximation implied by the abstract domains, the
--   computed counterexamples may actually not be realizable.
data ObservableCounterexample sym arch =
  ObservableCounterexample
    (RegsCounterExample sym arch)
    [MT.MemEvent sym (MM.ArchAddrWidth arch)]
    [MT.MemEvent sym (MM.ArchAddrWidth arch)]